#!/usr/bin/env python3
"""
Evernote .enex to Mealie Recipe Converter

This script converts Evernote .enex files to Mealie-compatible JSON format.
It parses recipe content and adds required Mealie metadata.
"""

import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import re
from datetime import datetime
import argparse
import uuid
from typing import Dict, List, Optional, Any
import html
import zipfile
import tempfile
import shutil


class EvernoteToMealieConverter:
    def __init__(self, input_dir: str, output_file: str):
        self.input_dir = Path(input_dir)
        self.output_file = Path(output_file)
        if not self.output_file.suffix:
            self.output_file = self.output_file.with_suffix('.zip')

        # Create temporary directory for processing
        self.temp_dir = Path(tempfile.mkdtemp())
        self.recipes_dir = self.temp_dir / "recipes"
        self.recipes_dir.mkdir(exist_ok=True)

    def convert_all_files(self):
        """Convert all .enex files in the input directory and create Mealie export zip"""
        try:
            enex_files = list(self.input_dir.glob("*.enex"))

            if not enex_files:
                print(f"No .enex files found in {self.input_dir}")
                return

            print(f"Found {len(enex_files)} .enex files to convert")

            all_recipes = []
            for enex_file in enex_files:
                print(f"Processing: {enex_file.name}")
                recipes = self.convert_enex_file(enex_file)
                all_recipes.extend(recipes)

            # Create Mealie export structure
            self.create_mealie_export_zip(all_recipes)

        finally:
            # Clean up temporary directory
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def convert_enex_file(self, enex_file: Path) -> List[Dict[str, Any]]:
        """Convert a single .enex file to Mealie format and return list of recipes"""
        recipes = []
        try:
            tree = ET.parse(enex_file)
            root = tree.getroot()

            # Parse notes from the ENEX file
            notes = root.findall('.//note')

            for i, note in enumerate(notes):
                recipe_data = self.parse_note_to_recipe(note)
                if recipe_data:
                    recipes.append(recipe_data)

                    # Create individual recipe JSON file with smart filename
                    safe_title = self.sanitize_filename(recipe_data['name'])
                    output_file = self.recipes_dir / f"{safe_title}.json"

                    # Handle duplicate filenames by adding counter
                    counter = 1
                    original_safe_title = safe_title
                    while output_file.exists():
                        safe_title = f"{original_safe_title}_{counter}"
                        output_file = self.recipes_dir / f"{safe_title}.json"
                        counter += 1

                    # Write the recipe JSON
                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(recipe_data, f, indent=2, ensure_ascii=False)

                    print(f"  Created: {output_file.name} (from: '{recipe_data['name']}')")

        except ET.ParseError as e:
            print(f"Error parsing {enex_file}: {e}")
        except Exception as e:
            print(f"Error processing {enex_file}: {e}")

        return recipes

    def parse_note_to_recipe(self, note: ET.Element) -> Optional[Dict[str, Any]]:
        """Parse an Evernote note into a Mealie recipe format"""
        try:
            title_elem = note.find('title')
            content_elem = note.find('content')
            created_elem = note.find('created')

            if title_elem is None or content_elem is None:
                return None

            title = title_elem.text or "Untitled Recipe"
            content = content_elem.text or ""

            # Parse the content (which is typically ENML - Evernote Markup Language)
            parsed_content = self.parse_enml_content(content)

            # Extract recipe components
            ingredients = self.extract_ingredients(parsed_content)
            instructions = self.extract_instructions(parsed_content)
            description = self.extract_description(parsed_content)

            # Get creation date safely
            created_date = None
            if created_elem is not None and created_elem.text:
                created_date = created_elem.text

            # Create Mealie-compatible recipe structure
            recipe = {
                "id": str(uuid.uuid4()),
                "name": title,
                "description": description,
                "recipeCategory": [],
                "tags": [],
                "recipeYield": "",
                "recipeIngredient": ingredients,
                "recipeInstructions": instructions,
                "nutrition": {},
                "tools": [],
                "slug": self.create_slug(title),
                "image": "",
                "totalTime": "",
                "prepTime": "",
                "cookTime": "",
                "performTime": "",
                "dateAdded": self.parse_date(created_date),
                "dateUpdated": datetime.now().isoformat(),
                "extras": {},
                "settings": {
                    "public": True,
                    "showNutrition": True,
                    "showAssets": True,
                    "landscapeView": False,
                    "disableAmount": True,
                    "disableComments": False
                },
                "assets": [],
                "notes": [
                    {
                        "id": str(uuid.uuid4()),
                        "title": "Imported from Evernote",
                        "text": "This recipe was imported from an Evernote .enex file"
                    }
                ],
                "comments": [],
                "rating": None,
                "orgURL": ""
            }

            return recipe

        except Exception as e:
            print(f"Error parsing note: {e}")
            return None

    def parse_enml_content(self, content: str) -> str:
        """Parse ENML (Evernote Markup Language) content"""
        if not content:
            return ""

        # Unescape HTML entities
        content = html.unescape(content)

        # Remove ENML wrapper and convert basic tags
        content = re.sub(r'<\?xml[^>]*\?>', '', content)
        content = re.sub(r'<!DOCTYPE[^>]*>', '', content)
        content = re.sub(r'<en-note[^>]*>', '', content)
        content = re.sub(r'</en-note>', '', content)

        # Convert common ENML tags to HTML
        content = re.sub(r'<en-todo[^>]*checked="true"[^>]*>', '☑ ', content)
        content = re.sub(r'<en-todo[^>]*>', '☐ ', content)
        content = re.sub(r'</en-todo>', '', content)

        # Remove remaining XML tags but keep content
        content = re.sub(r'<[^>]+>', ' ', content)

        # Clean up whitespace
        content = re.sub(r'\s+', ' ', content).strip()

        return content

    def extract_ingredients(self, content: str) -> List[str]:
        """Extract ingredients from the content"""
        ingredients = []

        # Look for common ingredient patterns
        lines = content.split('\n')

        # Try to find ingredients section
        ingredient_section = False
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if this line indicates start of ingredients
            if re.search(r'\b(ingredient|recipe|materials?)\b', line.lower()):
                ingredient_section = True
                continue

            # Check if this line indicates end of ingredients
            if re.search(r'\b(instruction|direction|method|step)\b', line.lower()):
                ingredient_section = False
                continue

            # If we're in ingredients section or line looks like an ingredient
            if ingredient_section or self.looks_like_ingredient(line):
                # Clean up the line
                clean_line = re.sub(r'^[•\-\*☐☑]\s*', '', line)
                if clean_line and len(clean_line) > 2:
                    ingredients.append(clean_line)

        # If no ingredients found using section method, try pattern matching
        if not ingredients:
            ingredients = self.extract_ingredients_by_pattern(content)

        return ingredients[:20]  # Limit to reasonable number

    def looks_like_ingredient(self, line: str) -> bool:
        """Check if a line looks like an ingredient"""
        # Common ingredient patterns
        patterns = [
            r'\d+\s*(cup|tbsp|tsp|lb|oz|gram|kg|ml|liter)',  # Measurements
            r'\d+\s*\w+\s+\w+',  # Number + words
            r'^[•\-\*☐☑]\s*\w+',  # Bullet points
        ]

        for pattern in patterns:
            if re.search(pattern, line.lower()):
                return True

        return False

    def extract_ingredients_by_pattern(self, content: str) -> List[str]:
        """Extract ingredients using pattern matching"""
        ingredients = []

        # Split content into potential ingredient lines
        lines = re.split(r'[.\n]', content)

        for line in lines:
            line = line.strip()
            if self.looks_like_ingredient(line) and len(line) < 100:
                ingredients.append(line)

        return ingredients

    def extract_instructions(self, content: str) -> List[Dict[str, Any]]:
        """Extract cooking instructions from the content"""
        instructions = []

        # Split content into sentences/steps
        steps = re.split(r'[.\n]', content)

        instruction_words = ['cook', 'bake', 'mix', 'add', 'heat', 'stir', 'pour', 'place', 'remove', 'serve']

        step_number = 1
        for step in steps:
            step = step.strip()
            if len(step) > 20 and any(word in step.lower() for word in instruction_words):
                instructions.append({
                    "id": str(uuid.uuid4()),
                    "title": f"Step {step_number}",
                    "text": step,
                    "ingredientReferences": []
                })
                step_number += 1

        # If no instructions found, create a general one
        if not instructions:
            instructions.append({
                "id": str(uuid.uuid4()),
                "title": "Instructions",
                "text": "Please refer to the original recipe content for detailed instructions.",
                "ingredientReferences": []
            })

        return instructions[:10]  # Limit to reasonable number

    def extract_description(self, content: str) -> str:
        """Extract recipe description"""
        # Take first few sentences as description
        sentences = re.split(r'[.!?]', content)
        description_parts = []

        for sentence in sentences[:3]:
            sentence = sentence.strip()
            if len(sentence) > 10 and not self.looks_like_ingredient(sentence):
                description_parts.append(sentence)

        description = '. '.join(description_parts)
        if description:
            description += '.'

        return description[:500]  # Limit length

    def create_slug(self, title: str) -> str:
        """Create a URL-friendly slug from title"""
        # Convert to lowercase
        slug = title.lower()

        # First, replace " (" (space + opening paren) with dash
        slug = re.sub(r' \(', '-', slug)

        # Remove special characters including remaining parentheses
        slug = re.sub(r'[^\w\s-]', '', slug)

        # Replace spaces and multiple hyphens with single hyphen
        slug = re.sub(r'[-\s]+', '-', slug)

        # Remove leading/trailing hyphens
        slug = slug.strip('-')

        # Ensure slug isn't empty
        if not slug:
            slug = "recipe"

        return slug[:100]  # Limit length

    def sanitize_filename(self, filename: str) -> str:
        """Create a safe filename without spaces, parentheses, and special characters"""
        # First, replace " (" (space + opening paren) with dash
        filename = re.sub(r' \(', '-', filename)

        # Remove or replace other problematic characters (including remaining parentheses)
        filename = re.sub(r'[<>:"/\\|?*()&%$#@!+={}[\];,]', '', filename)

        # Replace remaining spaces with underscores
        filename = re.sub(r'\s+', '_', filename)

        # Replace multiple underscores with single underscore
        filename = re.sub(r'_{2,}', '_', filename)

        # Remove leading/trailing underscores and dots
        filename = filename.strip('_.')

        # Ensure filename isn't empty
        if not filename:
            filename = "recipe"

        # Limit length and ensure it doesn't end with underscore
        filename = filename[:100].rstrip('_')

        return filename

    def parse_date(self, date_str: Optional[str]) -> str:
        """Parse Evernote date format to ISO format"""
        if not date_str:
            return datetime.now().isoformat()

    def create_mealie_export_zip(self, recipes: List[Dict[str, Any]]):
        """Create a Mealie-compatible export zip file"""
        print(f"\nCreating Mealie export zip with {len(recipes)} recipes...")

        # Create export metadata
        export_metadata = {
            "version": "1.0.0",
            "exported_at": datetime.now().isoformat(),
            "exported_by": "Evernote to Mealie Converter",
            "total_recipes": len(recipes),
            "migration_type": "evernote_import"
        }

        # Write export metadata
        metadata_file = self.temp_dir / "export_metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(export_metadata, f, indent=2, ensure_ascii=False)

        # Create recipe summary/index
        recipe_index = []
        for recipe in recipes:
            recipe_index.append({
                "id": recipe["id"],
                "name": recipe["name"],
                "slug": recipe["slug"],
                "dateAdded": recipe["dateAdded"],
                "recipeCategory": recipe["recipeCategory"],
                "tags": recipe["tags"]
            })

        index_file = self.temp_dir / "recipe_index.json"
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(recipe_index, f, indent=2, ensure_ascii=False)

        # Create the zip file with Mealie export structure
        with zipfile.ZipFile(self.output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add metadata files
            zipf.write(metadata_file, "export_metadata.json")
            zipf.write(index_file, "recipe_index.json")

            # Add all recipe files
            for recipe_file in self.recipes_dir.glob("*.json"):
                zipf.write(recipe_file, f"recipes/{recipe_file.name}")

            # Create empty directories that Mealie expects
            zipf.writestr("images/", "")
            zipf.writestr("assets/", "")

        print(f"Created Mealie export zip: {self.output_file}")
        print(f"Total recipes exported: {len(recipes)}")
        print(f"You can now import this zip file directly into Mealie using the bulk import feature.")

        try:
            # Evernote dates are typically in format: 20231201T123000Z
            if 'T' in date_str:
                dt = datetime.strptime(date_str[:15], '%Y%m%dT%H%M%S')
                return dt.isoformat()
        except ValueError:
            pass

        return datetime.now().isoformat()


def main():
    parser = argparse.ArgumentParser(description='Convert Evernote .enex files to Mealie export zip format')
    parser.add_argument('input_dir', help='Directory containing .enex files')
    parser.add_argument('output_file', help='Output zip file path (e.g., mealie_recipes.zip)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    if not os.path.exists(args.input_dir):
        print(f"Error: Input directory '{args.input_dir}' does not exist")
        return 1

    converter = EvernoteToMealieConverter(args.input_dir, args.output_file)

    try:
        converter.convert_all_files()
        print(f"\nConversion complete! Your Mealie export zip is ready: {converter.output_file}")
        print("Import this zip file into Mealie using: Settings > Data Management > Import Recipes > Upload File")
    except Exception as e:
        print(f"Error during conversion: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())

