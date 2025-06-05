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
import base64
import hashlib


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
            
            # Parse content and extract images
            text_content, images = self.parse_content_and_images(content, note)
            ingredients = self.extract_ingredients(text_content)
            instructions = self.extract_instructions(text_content)
            description = self.extract_description(text_content)
            source_url = self.extract_source_url(text_content)
            
            # Create recipe data without image filenames first
            self.recipe_counter += 1
            recipe_data = self.create_recipe_data(
                self.recipe_counter, title, description, 
                ingredients, instructions, created, [], source_url
            )
            
            # Create recipe directory with images
            return self.create_recipe_dir(self.recipe_counter, recipe_data, title, images, note)
            
        except Exception as e:
            print(f"Error processing note: {e}")
            return None

    def create_recipe_data(self, recipe_id: int, name: str, description: str,
                          ingredients: List[str], instructions: List[str], 
                          created: Optional[str], image_files: List[str] = None, 
                          source_url: str = "") -> Dict:
        """Create Nextcloud Recipes JSON-LD format"""
        
        # Process instructions to handle image placeholders
        processed_instructions = []
        for instruction in instructions:
            if instruction.strip():
                # Check if this is an image placeholder
                image_match = re.match(r'\[IMAGE_(\d+)\]', instruction)
                if image_match:
                    image_index = int(image_match.group(1))
                    if image_files and image_index < len(image_files):
                        # Create an instruction that references the image
                        processed_instructions.append({
                            "@type": "HowToStep",
                            "text": f"See image: {image_files[image_index]}",
                            "image": image_files[image_index]
                        })
                    else:
                        # Fallback if image not found
                        processed_instructions.append({
                            "@type": "HowToStep",
                            "text": "[Image reference]"
                        })
                else:
                    # Regular text instruction
                    processed_instructions.append({
                        "@type": "HowToStep", 
                        "text": instruction.strip()
                    })
        
        # Create Nextcloud Recipes format using Recipe schema.org structure
        recipe = {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": name,
            "description": description or "Recipe imported from Evernote",
            "image": "",  # Will be updated later with main recipe image
            "recipeYield": "4",
            "prepTime": "PT15M",
            "cookTime": "PT30M", 
            "totalTime": "PT45M",
            "recipeCategory": "Imported",
            "recipeCuisine": "",
            "keywords": "imported, evernote",
            "recipeIngredient": [ingredient.strip() for ingredient in ingredients if ingredient.strip()],
            "recipeInstructions": processed_instructions,
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
            "url": source_url,
            "orgURL": source_url
        }
        
        return recipe

    def create_recipe_dir(self, recipe_id: int, recipe_data: Dict, title: str, images: List[Dict], note: ET.Element) -> Path:
        """Create individual recipe directory for Nextcloud Recipes with images"""
        # Create safe directory name
        safe_title = re.sub(r'[^\w\s-]', '', title).strip()
        safe_title = re.sub(r'[\s]+', '_', safe_title)
        recipe_dir_name = f"{safe_title}_{recipe_id}"
        
        # Create recipe directory
        recipe_dir = self.temp_dir / recipe_dir_name
        recipe_dir.mkdir(exist_ok=True)
        
        # Extract and save only the first image
        image_filenames = []
        if images:  # Only process the first image
            try:
                # Get the first image
                image_info = images[0]
                
                # Save image file with "full" name
                image_filename = f"full.{image_info['ext']}"
                image_path = recipe_dir / image_filename
                
                with open(image_path, 'wb') as f:
                    f.write(image_info['data'])
                
                image_filenames.append(image_filename)
                print(f"    Saved image: {image_filename}")
                
            except Exception as e:
                print(f"    Error saving image: {e}")
        
        # Update recipe data with actual image filenames and regenerate instructions
        # Get the text content again to extract original instructions with placeholders
        title_elem = note.find('title')
        content_elem = note.find('content')
        content = content_elem.text if content_elem is not None else ""
        
        # Re-parse content to get instructions with image placeholders
        text_content, _ = self.parse_content_and_images(content, note)
        original_instructions = self.extract_instructions(text_content)
        
        # Recreate the recipe data with proper image filenames
        updated_recipe_data = self.create_recipe_data(
            recipe_id, recipe_data["name"], recipe_data["description"], 
            recipe_data["recipeIngredient"], original_instructions,
            recipe_data.get("dateCreated"), image_filenames, recipe_data.get("url", "")
        )
        
        # Set the main recipe image (only first image if available)
        if image_filenames:
            # Only put the first image as the main recipe image
            updated_recipe_data["image"] = image_filenames[0]
        else:
            updated_recipe_data["image"] = ""
        
        # Create recipe.json file in the directory
        json_file = recipe_dir / "recipe.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(updated_recipe_data, f, indent=2, ensure_ascii=False)
        
        print(f"  Recipe {recipe_id}: {title} ({len(image_filenames)} images)")
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

    def parse_content_and_images(self, content: str, note: ET.Element) -> tuple[str, List[Dict]]:
        """Parse ENML to plain text and extract images with their positions"""
        if not content:
            return "", []
        
        # Extract images from resources in the note and create a mapping
        images = []
        image_hash_to_data = {}
        resources = note.findall('.//resource')
        
        for resource in resources:
            try:
                # Get the resource data
                data_elem = resource.find('data')
                mime_elem = resource.find('mime')
                
                if data_elem is not None and mime_elem is not None:
                    # Check if it's an image
                    mime_type = mime_elem.text
                    if mime_type and mime_type.startswith('image/'):
                        # Get the hash attribute for matching with en-media tags
                        # Try multiple ways to get the hash
                        resource_hash = None
                        
                        # Check data element attributes first
                        if hasattr(data_elem, 'attrib'):
                            for attr_name, attr_value in data_elem.attrib.items():
                                if 'hash' in attr_name.lower():
                                    resource_hash = attr_value
                                    break
                        
                        # Check resource element attributes
                        if not resource_hash and hasattr(resource, 'attrib'):
                            for attr_name, attr_value in resource.attrib.items():
                                if 'hash' in attr_name.lower():
                                    resource_hash = attr_value
                                    break
                        
                        # If still no hash found, create one from the data
                        if not resource_hash:
                            resource_hash = hashlib.md5(data_elem.text.encode()).hexdigest()
                        
                        print(f"    Found image with hash: {resource_hash[:8]}...")
                        
                        # Decode base64 image data
                        image_data = base64.b64decode(data_elem.text)
                        
                        # Determine file extension from mime type
                        ext_map = {
                            'image/jpeg': 'jpg',
                            'image/jpg': 'jpg', 
                            'image/png': 'png',
                            'image/gif': 'gif',
                            'image/webp': 'webp',
                            'image/bmp': 'bmp'
                        }
                        ext = ext_map.get(mime_type, 'jpg')
                        
                        image_info = {
                            'data': image_data,
                            'mime': mime_type,
                            'ext': ext,
                            'hash': resource_hash
                        }
                        
                        images.append(image_info)
                        image_hash_to_data[resource_hash] = len(images) - 1
                        
            except Exception as e:
                print(f"    Error processing image: {e}")
                continue
        
        # Parse text content and replace en-media tags with image placeholders
        text_content = self.parse_content_with_image_placeholders(content, image_hash_to_data)
        
        return text_content, images

    def parse_content_with_image_placeholders(self, content: str, image_hash_to_data: Dict) -> str:
        """Parse ENML to plain text and insert image placeholders where images appear"""
        if not content:
            return ""
        
        # Decode HTML entities
        content = html.unescape(content)
        
        # Remove ENML wrapper
        content = re.sub(r'<\?xml[^>]*\?>', '', content)
        content = re.sub(r'<!DOCTYPE[^>]*>', '', content)
        content = re.sub(r'<en-note[^>]*>', '', content)
        content = re.sub(r'</en-note>', '', content)
        
        # Replace en-media tags with image placeholders
        def replace_media(match):
            hash_attr = match.group(1)
            print(f"    Found en-media tag with hash: {hash_attr[:8]}...")
            if hash_attr in image_hash_to_data:
                image_index = image_hash_to_data[hash_attr]
                print(f"    Replacing with IMAGE_{image_index}")
                return f"\n[IMAGE_{image_index}]\n"
            else:
                print(f"    Hash not found in mapping")
            return "\n[IMAGE]\n"
        
        content = re.sub(r'<en-media[^>]*hash="([^"]*)"[^>]*/?>', replace_media, content)
        
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

    def extract_source_url(self, content: str) -> str:
        """Extract potential recipe source URL from content"""
        if not content:
            return ""
        
        print(f"    Processing content length: {len(content)}")
        print(f"    Content preview: {content[:200]}...")
        
        # Look for URLs with multiple patterns to catch edge cases
        url_patterns = [
            r'https?://[^\s<>"\']+\.[^\s<>"\'\)\]]*',  # Standard pattern
            r'https?://(?:www\.)?seriouseats\.com[^\s<>"\']*',  # SeriousEats with or without www
            r'https?://www\.seriouseats\.com[^\s<>"\']*',  # Specific for www.SeriousEats
            r'https?://seriouseats\.com[^\s<>"\']*',  # SeriousEats without www
            r'https?://[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}[^\s<>"\']*'  # More flexible domain pattern
        ]
        
        all_urls = []
        for pattern in url_patterns:
            urls = re.findall(pattern, content)
            all_urls.extend(urls)
        
        # Remove duplicates while preserving order
        urls = []
        seen = set()
        for url in all_urls:
            if url not in seen:
                urls.append(url)
                seen.add(url)
        
        if not urls:
            print("    No URLs found")
            return ""
        
        print(f"    Found URLs: {urls}")
        
        # Score URLs based on how likely they are to be recipe sources
        recipe_keywords = [
            'recipe', 'food', 'cooking', 'kitchen', 'chef', 'cuisine', 'dish',
            'allrecipes', 'foodnetwork', 'epicurious', 'bonappetit', 'seriouseats',
            'tasteofhome', 'delish', 'food52', 'yummly', 'budget', 'meal',
            'ingredient', 'bake', 'cook', 'serious', 'eats'
        ]
        
        scored_urls = []
        for url in urls:
            score = 0
            url_lower = url.lower()
            
            # Higher score for recipe-related domains/paths
            for keyword in recipe_keywords:
                if keyword in url_lower:
                    score += 2
            
            # Boost score for common recipe sites
            if any(site in url_lower for site in ['allrecipes.com', 'foodnetwork.com', 
                                                  'epicurious.com', 'bonappetit.com',
                                                  'seriouseats.com', 'food52.com', 
                                                  'tasteofhome.com']):
                score += 5
            
            # Extra boost for SeriousEats specifically
            if 'seriouseats.com' in url_lower:
                score += 3
            
            # Penalize very long URLs or those with tracking parameters
            if len(url) > 150 or any(param in url_lower for param in ['utm_', 'ref=', 'src=']):
                score -= 1
            
            scored_urls.append((score, url))
            print(f"    URL: {url[:70]}... Score: {score}")
        
        # Return the highest scoring URL, or first URL if no good matches
        if scored_urls:
            scored_urls.sort(key=lambda x: x[0], reverse=True)
            best_url = scored_urls[0][1]
            
            # Clean up the URL (remove trailing punctuation)
            best_url = re.sub(r'[.,;!?\)\]]+$', '', best_url)
            
            print(f"    Selected URL: {best_url}")
            return best_url
        
        return ""

    def extract_ingredients(self, content: str) -> List[str]:
        """Extract ingredients from content"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        ingredients = []
        
        # Look for ingredient section
        in_ingredients_section = False
        
        for line in lines:
            # Skip URLs completely
            if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                continue
                
            # Skip page numbers and references
            if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                continue
                
            # Skip serving/yield info
            if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
                continue
                
            # Skip time information
            if re.search(r'\b(prep|cook|total)\s+time\b|\b\d+\s+(min|minutes|hrs?|hours?)\b', line.lower()):
                continue
            
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
                # Apply same filters
                if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                    continue
                if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                    continue
                if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
                    continue
                if re.search(r'\b(prep|cook|total)\s+time\b|\b\d+\s+(min|minutes|hrs?|hours?)\b', line.lower()):
                    continue
                    
                if self.is_ingredient_line(line) and len(line) < 200:
                    clean_line = self.clean_ingredient_line(line)
                    if clean_line and len(clean_line) > 2:
                        ingredients.append(clean_line)
        
        # If still no ingredients, use first few short lines (but apply filters)
        if not ingredients:
            for line in lines[:10]:
                if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                    continue
                if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                    continue
                if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
                    continue
                if re.search(r'\b(prep|cook|total)\s+time\b|\b\d+\s+(min|minutes|hrs?|hours?)\b', line.lower()):
                    continue
                    
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
        
        # Exclude obvious non-ingredients
        if any(exclude in line_lower for exclude in [
            'http', 'www.', '.com', '.org', '.net',  # URLs
            'page', 'serves', 'serving', 'yield',    # Serving info
            'prep time', 'cook time', 'total time',  # Time info
            'preheat', 'oven to', 'degrees',         # Cooking instructions
            'recipe from', 'source:', 'adapted',     # Source info
            'step', 'instruction', 'direction'       # Instruction headers
        ]):
            return False
        
        # Skip lines that are too long (likely instructions)
        if len(line) > 120:
            return False
        
        # Skip lines with cooking verbs that indicate instructions
        cooking_verbs = ['preheat', 'heat', 'cook', 'bake', 'boil', 'simmer', 'sauté', 'fry', 'mix thoroughly', 'combine all', 'whisk until', 'beat until']
        if any(verb in line_lower for verb in cooking_verbs):
            return False
        
        # Common ingredient patterns
        patterns = [
            r'\d+\s*(cup|cups|tbsp|tablespoons|tsp|teaspoons|lb|lbs|pound|pounds|oz|ounces|g|grams|kg|ml|liter|liters)\b',
            r'\d+/\d+\s*(cup|cups|tbsp|tablespoons|tsp|teaspoons)',  # Fractions
            r'^[•\-\*☐✓]\s*\w+',  # bullet point followed by word
            r'^\d+[\.\)]\s*[a-zA-Z]',  # numbered list with ingredient
            r'\b(salt|pepper|sugar|flour|oil|butter|onion|garlic|cheese|milk|egg|eggs|water|chicken|beef|pork|fish)\b'
        ]
        
        return any(re.search(pattern, line_lower) for pattern in patterns)

    def extract_instructions(self, content: str) -> List[str]:
        """Extract cooking instructions with inline images"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        instructions = []
        
        # Look for instruction patterns and include image placeholders
        for line in lines:
            # Check if this is an image placeholder
            if re.match(r'\[IMAGE_\d+\]', line):
                instructions.append(line)  # Keep image placeholders as separate instructions
            elif self.is_instruction_line(line) and len(line) > 15:
                clean_line = self.clean_instruction_line(line)
                if clean_line:
                    instructions.append(clean_line)
        
        # Fallback: use longer lines that aren't ingredients
        if not instructions:
            for line in lines:
                if re.match(r'\[IMAGE_\d+\]', line):
                    instructions.append(line)
                elif (len(line) > 25 and 
                      not self.is_ingredient_line(line) and
                      len(line) < 500):
                    instructions.append(line)
        
        # Ensure at least one instruction
        if not instructions:
            instructions.append("Follow the original recipe instructions from your Evernote note.")
        
        return instructions[:30]  # Increased limit to accommodate images

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
