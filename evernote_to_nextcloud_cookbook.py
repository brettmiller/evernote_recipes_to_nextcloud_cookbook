#!/usr/bin/env python3
"""
Evernote .enex to Nextcloud Recipes Export Converter

This script converts Evernote .enex files to Nextcloud Recipes export format.
Creates individual JSON files for each recipe using Recipe schema.
"""

import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import re
from datetime import datetime
import argparse
import zipfile
import tempfile
import shutil
from typing import Dict, List, Optional, Any
import html


class EvernoteToNextcloudConverter:
    def __init__(self, input_dir: str, output_file: str):
        self.input_dir = Path(input_dir)
        self.output_file = Path(output_file)
        if not self.output_file.suffix:
            self.output_file = self.output_file.with_suffix('.zip')
        
        # Create temporary directory
        self.temp_dir = Path(tempfile.mkdtemp())
        self.recipe_counter = 0

    def convert(self):
        """Main conversion method"""
        try:
            enex_files = list(self.input_dir.glob("*.enex"))
            
            if not enex_files:
                print(f"No .enex files found in {self.input_dir}")
                return
            
            print(f"Found {len(enex_files)} .enex files")
            
            recipe_zips = []
            
            for enex_file in enex_files:
                print(f"Processing: {enex_file.name}")
                dirs = self.process_enex_file(enex_file)
                recipe_zips.extend(dirs)
            
            if recipe_zips:
                self.create_export_zip(recipe_zips)
            else:
                print("No recipes found to export")
            
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def process_enex_file(self, enex_file: Path) -> List[Path]:
        """Process a single .enex file"""
        recipe_zips = []
        
        try:
            tree = ET.parse(enex_file)
            root = tree.getroot()
            notes = root.findall('.//note')
            
            for note in notes:
                recipe_zip = self.process_note(note)
                if recipe_zip:
                    recipe_zips.append(recipe_zip)
                    
        except Exception as e:
            print(f"Error processing {enex_file}: {e}")
            
        return recipe_zips

    def process_note(self, note: ET.Element) -> Optional[Path]:
        """Convert a note to a recipe zip"""
        try:
            # Extract basic note data
            title_elem = note.find('title')
            content_elem = note.find('content')
            created_elem = note.find('created')
            
            if title_elem is None or content_elem is None:
                return None
                
            title = title_elem.text or "Untitled Recipe"
            content = content_elem.text or ""
            created = created_elem.text if created_elem is not None else None
            
            # Parse content
            text_content = self.parse_content(content)
            ingredients = self.extract_ingredients(text_content)
            instructions = self.extract_instructions(text_content)
            description = self.extract_description(text_content)
            
            # Create recipe data in simplified format
            self.recipe_counter += 1
            recipe_data = self.create_recipe_data(
                self.recipe_counter, title, description, 
                ingredients, instructions, created
            )
            
            # Create recipe zip
            return self.create_recipe_zip(self.recipe_counter, recipe_data, title)
            
        except Exception as e:
            print(f"Error processing note: {e}")
            return None

    def create_recipe_data(self, recipe_id: int, name: str, description: str,
                          ingredients: List[str], instructions: List[str], 
                          created: Optional[str]) -> Dict:
        """Create Nextcloud Recipes JSON-LD format"""
        
        # Create Nextcloud Recipes format using Recipe schema.org structure
        recipe = {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": name,
            "description": description or "Recipe imported from Evernote",
            "image": [],
            "recipeYield": "4",
            "prepTime": "PT15M",
            "cookTime": "PT30M", 
            "totalTime": "PT45M",
            "recipeCategory": "Imported",
            "recipeCuisine": "",
            "keywords": "imported, evernote",
            "recipeIngredient": [ingredient.strip() for ingredient in ingredients if ingredient.strip()],
            "recipeInstructions": [
                {
                    "@type": "HowToStep",
                    "text": instruction.strip()
                }
                for instruction in instructions if instruction.strip()
            ],
            "nutrition": {
                "@type": "NutritionInformation",
                "calories": None,
                "fatContent": None,
                "proteinContent": None,
                "carbohydrateContent": None
            },
            "tool": [],
            "dateCreated": self.format_datetime(created),
            "dateModified": self.format_datetime(None),
            "url": ""
        }
        
        return recipe

    def create_recipe_zip(self, recipe_id: int, recipe_data: Dict, title: str) -> Path:
        """Create individual recipe directory for Nextcloud Recipes"""
        # Create safe directory name
        safe_title = re.sub(r'[^\w\s-]', '', title).strip()
        safe_title = re.sub(r'[\s]+', '_', safe_title)
        recipe_dir_name = f"{safe_title}_{recipe_id}"
        
        # Create recipe directory
        recipe_dir = self.temp_dir / recipe_dir_name
        recipe_dir.mkdir(exist_ok=True)
        
        # Create recipe.json file in the directory
        json_file = recipe_dir / "recipe.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(recipe_data, f, indent=2, ensure_ascii=False)
        
        print(f"  Recipe {recipe_id}: {title}")
        return recipe_dir

    def create_export_zip(self, recipe_dirs: List[Path]):
        """Create main export zip with recipe directories"""
        with zipfile.ZipFile(self.output_file, 'w', zipfile.ZIP_DEFLATED) as main_zip:
            for recipe_dir in recipe_dirs:
                # Add all files in the recipe directory to the zip
                for file_path in recipe_dir.rglob('*'):
                    if file_path.is_file():
                        # Create the archive path: recipe_dir_name/filename
                        archive_path = recipe_dir.name + '/' + file_path.relative_to(recipe_dir).as_posix()
                        main_zip.write(file_path, archive_path)
        
        print(f"\nExport created: {self.output_file}")
        print(f"Recipes: {len(recipe_dirs)}")
        print("Import into Nextcloud Recipes or other systems that support Schema.org Recipe format")

    def parse_content(self, content: str) -> str:
        """Parse ENML to plain text"""
        if not content:
            return ""
        
        # Decode HTML entities
        content = html.unescape(content)
        
        # Remove ENML wrapper
        content = re.sub(r'<\?xml[^>]*\?>', '', content)
        content = re.sub(r'<!DOCTYPE[^>]*>', '', content)
        content = re.sub(r'<en-note[^>]*>', '', content)
        content = re.sub(r'</en-note>', '', content)
        
        # Handle checkboxes and convert to unicode
        content = re.sub(r'<en-todo[^>]*checked="true"[^>]*>', '✓ ', content)
        content = re.sub(r'<en-todo[^>]*>', '☐ ', content)
        
        # Convert line breaks and divs to newlines
        content = re.sub(r'<br[^>]*>', '\n', content)
        content = re.sub(r'<div[^>]*>', '\n', content)
        content = re.sub(r'</div>', '', content)
        
        # Remove remaining HTML tags
        content = re.sub(r'<[^>]+>', '\n', content)
        
        # Clean up whitespace
        content = re.sub(r'\n+', '\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = re.sub(r'^\s+|\s+$', '', content, flags=re.MULTILINE)
        
        return content.strip()

    def extract_ingredients(self, content: str) -> List[str]:
        """Extract ingredients from content"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        ingredients = []
        
        # Look for ingredient section
        in_ingredients_section = False
        
        for line in lines:
            # Check for ingredient section headers
            if re.search(r'\b(ingredient|材料)\b', line.lower()):
                in_ingredients_section = True
                continue
                
            # Check for instruction section headers (stop ingredient detection)
            if re.search(r'\b(instruction|direction|method|step|作り方|手順)\b', line.lower()):
                in_ingredients_section = False
                continue
            
            # Extract ingredient-like lines
            if in_ingredients_section or self.is_ingredient_line(line):
                if len(line) < 200:  # Reasonable ingredient length
                    clean_line = self.clean_ingredient_line(line)
                    if clean_line and len(clean_line) > 2:
                        ingredients.append(clean_line)
        
        # If no ingredients found, try pattern matching on all lines
        if not ingredients:
            for line in lines:
                if self.is_ingredient_line(line) and len(line) < 200:
                    clean_line = self.clean_ingredient_line(line)
                    if clean_line and len(clean_line) > 2:
                        ingredients.append(clean_line)
        
        # If still no ingredients, use first few short lines
        if not ingredients:
            for line in lines[:10]:
                if (5 < len(line) < 150 and 
                    not self.is_instruction_line(line)):
                    ingredients.append(line)
        
        return ingredients[:25]  # Limit to reasonable number

    def clean_ingredient_line(self, line: str) -> str:
        """Clean up an ingredient line"""
        # Remove bullet points and checkmarks
        line = re.sub(r'^[•\-\*☐✓]\s*', '', line)
        # Remove leading numbers with periods/parentheses
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        return line.strip()

    def is_ingredient_line(self, line: str) -> bool:
        """Check if line looks like an ingredient"""
        line_lower = line.lower()
        
        # Common ingredient patterns
        patterns = [
            r'\d+\s*(cup|tbsp|tsp|tablespoon|teaspoon|lb|pound|oz|ounce|g|gram|kg|ml|liter|l)\b',
            r'\d+\s*\w+',  # number followed by word
            r'^[•\-\*☐✓]\s*\w+',  # bullet point followed by word
            r'^\d+[\.\)]\s*\w+',  # numbered list
            r'\b(salt|pepper|sugar|flour|oil|butter|onion|garlic|cheese|milk|egg|water|chicken|beef)\b'
        ]
        
        return any(re.search(pattern, line_lower) for pattern in patterns)

    def extract_instructions(self, content: str) -> List[str]:
        """Extract cooking instructions"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        instructions = []
        
        # Look for instruction patterns
        for line in lines:
            if self.is_instruction_line(line) and len(line) > 15:
                clean_line = self.clean_instruction_line(line)
                if clean_line:
                    instructions.append(clean_line)
        
        # Fallback: use longer lines that aren't ingredients
        if not instructions:
            for line in lines:
                if (len(line) > 25 and 
                    not self.is_ingredient_line(line) and
                    len(line) < 500):
                    instructions.append(line)
        
        # Ensure at least one instruction
        if not instructions:
            instructions.append("Follow the original recipe instructions from your Evernote note.")
        
        return instructions[:20]  # Limit to reasonable number

    def clean_instruction_line(self, line: str) -> str:
        """Clean up an instruction line"""
        # Remove bullet points and checkmarks
        line = re.sub(r'^[•\-\*☐✓]\s*', '', line)
        # Remove leading numbers with periods/parentheses
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        return line.strip()

    def is_instruction_line(self, line: str) -> bool:
        """Check if line looks like an instruction"""
        line_lower = line.lower()
        
        instruction_keywords = [
            'cook', 'bake', 'mix', 'add', 'heat', 'stir', 'pour', 'place', 
            'remove', 'serve', 'prepare', 'combine', 'season', 'boil', 
            'simmer', 'fry', 'chop', 'slice', 'dice', 'mince', 'whisk',
            'blend', 'fold', 'beat', 'knead', 'roll', 'spread', 'brush',
            'drizzle', 'sprinkle', 'garnish', 'chill', 'freeze', 'thaw'
        ]
        
        return any(keyword in line_lower for keyword in instruction_keywords)

    def extract_description(self, content: str) -> str:
        """Extract recipe description"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        
        # Look for descriptive lines at the beginning
        description_lines = []
        for line in lines[:5]:
            if (len(line) > 20 and 
                not self.is_ingredient_line(line) and
                not self.is_instruction_line(line) and
                not re.search(r'\b(ingredient|instruction|direction|method|step)\b', line.lower())):
                description_lines.append(line)
        
        description = ' '.join(description_lines)
        
        # Limit length and clean up
        if len(description) > 500:
            description = description[:500] + "..."
        
        return description or "Recipe imported from Evernote"

    def format_datetime(self, date_str: Optional[str]) -> str:
        """Format datetime for recipe data"""
        if not date_str:
            return datetime.now().isoformat()
        
        try:
            # Evernote format: 20231201T123000Z
            if 'T' in date_str and len(date_str) >= 15:
                dt = datetime.strptime(date_str[:15], '%Y%m%dT%H%M%S')
                return dt.isoformat()
        except ValueError:
            pass
        
        return datetime.now().isoformat()


def main():
    parser = argparse.ArgumentParser(
        description='Convert Evernote .enex files to Nextcloud Recipes export format'
    )
    parser.add_argument('input_dir', help='Directory containing .enex files')
    parser.add_argument('output_file', help='Output zip file path')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_dir):
        print(f"Error: Directory '{args.input_dir}' not found")
        return 1
    
    exporter = EvernoteToNextcloudConverter(args.input_dir, args.output_file)
    
    try:
        exporter.convert()
        print("\nConversion completed successfully!")
        print("The export zip can be imported into Nextcloud Recipes or other systems that support Schema.org Recipe format.")
    except Exception as e:
        print(f"Conversion failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
