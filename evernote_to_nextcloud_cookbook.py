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
            
            print(f"\n{'='*80}")
            print(f"PROCESSING RECIPE: {title}")
            print(f"{'='*80}")
            
            # Parse content and extract images
            text_content, images = self.parse_content_and_images(content, note)
            ingredients = self.extract_ingredients(text_content, title)
            instructions = self.extract_instructions(text_content, title)
            description = self.extract_description(text_content)
            source_url = self.extract_source_url(text_content)
            
            # Post-process instructions to move misclassified ingredients back to ingredients list
            final_ingredients, final_instructions = self.post_process_ingredients_from_instructions(ingredients, instructions, title)
            
            print(f"\n{'='*80}")
            print(f"FINISHED PROCESSING: {title}")
            print(f"  - Ingredients: {len(final_ingredients)}")
            print(f"  - Instructions: {len(final_instructions)}")
            print(f"  - Images: {len(images)}")
            print(f"{'='*80}\n")
            
            # Create recipe data without image filenames first
            self.recipe_counter += 1
            recipe_data = self.create_recipe_data(
                self.recipe_counter, title, description, 
                final_ingredients, final_instructions, created, [], source_url
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
        original_instructions = self.extract_instructions(text_content, recipe_data["name"])
        
        # Apply post-processing to the original instructions to get the final clean list
        _, clean_instructions = self.post_process_ingredients_from_instructions([], original_instructions, recipe_data["name"])
        
        # Recreate the recipe data with proper image filenames
        updated_recipe_data = self.create_recipe_data(
            recipe_id, recipe_data["name"], recipe_data["description"], 
            recipe_data["recipeIngredient"], clean_instructions,
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

    def extract_ingredients(self, content: str, recipe_title: str = "Unknown Recipe") -> List[str]:
        """Extract ingredients from content"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        ingredients = []
        substitution_notes = []
        
        print(f"\n{'#'*80}")
        print(f"# INGREDIENT EXTRACTION - {recipe_title}")
        print(f"{'#'*80}")
        print(f"Total lines to process: {len(lines)}")
        
        # Show first 20 lines for debugging
        print("=== ALL LINES TO PROCESS ===")
        for i, line in enumerate(lines):
            print(f"  {i+1:2d}. '{line[:60]}{'...' if len(line) > 60 else ''}'")
        print("=" * 50)
        
        # First pass: collect substitution notes
        for line in lines:
            if self.is_substitution_note(line):
                substitution_notes.append(line)
        
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
            
            # CRITICAL FIX: Always check is_ingredient_line() first, even if in ingredients section
            clean_line = self.clean_ingredient_line(line)
            if not clean_line or len(clean_line) <= 2:
                print(f"REJECTED (too short after cleaning): '{line}' -> '{clean_line}'")
                continue
                
            # Apply the ingredient filtering logic - this is the key fix
            is_ingredient = self.is_ingredient_line(clean_line)
            
            # Enhanced logic: if we're in an ingredient section, be more lenient
            if in_ingredients_section and not is_ingredient:
                # Check if it's a simple ingredient that doesn't match our patterns
                # but is likely an ingredient based on context
                if (len(clean_line) > 3 and len(clean_line) < 100 and
                    not any(verb in clean_line.lower() for verb in ['heat', 'cook', 'bake', 'mix', 'stir', 'add', 'pour', 'remove']) and
                    not clean_line.lower().startswith(('step', 'then', 'next', 'meanwhile', 'after', 'before', 'until'))):
                    print(f"ACCEPTED (in ingredient section): '{clean_line}'")
                    enhanced_ingredient = self.enhance_ingredient_with_substitutions(clean_line, substitution_notes)
                    ingredients.append(enhanced_ingredient)
                    print(f"ADDED INGREDIENT (pass 1 - section context): '{enhanced_ingredient}'")
                    continue
            
            if is_ingredient and len(line) < 200:  # Must pass ingredient test AND length check
                # Try to match with substitution notes
                enhanced_ingredient = self.enhance_ingredient_with_substitutions(clean_line, substitution_notes)
                ingredients.append(enhanced_ingredient)
                print(f"ADDED INGREDIENT (pass 1): '{enhanced_ingredient}'")
            else:
                # Debug why this line was rejected
                if len(line) >= 200:
                    print(f"REJECTED (too long): '{line[:50]}...' ({len(line)} chars)")
                else:
                    print(f"REJECTED LINE: '{line[:60]}...' (is_ingredient={is_ingredient}, in_section={in_ingredients_section})")
        
        # If no ingredients found, try pattern matching on all lines
        if not ingredients:
            print("No ingredients found in pass 1, trying pass 2...")
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
                        enhanced_ingredient = self.enhance_ingredient_with_substitutions(clean_line, substitution_notes)
                        ingredients.append(enhanced_ingredient)
                        print(f"ADDED INGREDIENT (pass 2): '{enhanced_ingredient}'")
        
        # If still no ingredients, use first few short lines (but apply strict filters)
        if not ingredients:
            print("No ingredients found in pass 2, trying pass 3 (first 10 lines)...")
            for i, line in enumerate(lines[:10]):
                print(f"  Line {i+1}: '{line[:80]}...'")
                if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                    print(f"    REJECTED: Contains URL")
                    continue
                if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                    print(f"    REJECTED: Contains page reference")
                    continue
                if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
                    print(f"    REJECTED: Contains serving info")
                    continue
                if re.search(r'\b(prep|cook|total)\s+time\b|\b\d+\s+(min|minutes|hrs?|hours?)\b', line.lower()):
                    print(f"    REJECTED: Contains time info")
                    continue
                    
                # MUST pass the strict ingredient test - no fallback to instruction test
                if (5 < len(line) < 150 and 
                    self.is_ingredient_line(line)):  # Use strict ingredient test, not instruction test
                    enhanced_ingredient = self.enhance_ingredient_with_substitutions(line, substitution_notes)
                    ingredients.append(enhanced_ingredient)
                    print(f"    ADDED INGREDIENT (pass 3): '{enhanced_ingredient}'")
                else:
                    is_ingredient = self.is_ingredient_line(line)
                    print(f"    REJECTED: length={len(line)}, is_ingredient={is_ingredient}")
        
        print(f"=== FINAL INGREDIENT COUNT: {len(ingredients)} ===")
        for i, ing in enumerate(ingredients):
            print(f"  {i+1}. {ing}")
        print("=" * 50)
        print(f"# END INGREDIENT EXTRACTION - {recipe_title}")
        print(f"{'#'*80}")
        
        return ingredients[:25]  # Limit to reasonable number

    def clean_ingredient_line(self, line: str) -> str:
        """Clean up an ingredient line"""
        # Remove various bullet points and list markers
        line = re.sub(r'^[\s]*[•\-\*\+\>\◦\▪\▫\○\●\□\■\➤\→\⁃]\s*', '', line)
        # Remove Unicode bullet points and dashes
        line = re.sub(r'^[\s]*[‣‧⁌⁍]\s*', '', line)
        # Remove checkmarks and checkboxes
        line = re.sub(r'^[\s]*[☐✓✗□✔✘]\s*', '', line)
        # Remove leading numbers with periods/parentheses/brackets
        line = re.sub(r'^\d+[\.\)\]]\s*', '', line)
        # Remove leading letters with periods/parentheses
        line = re.sub(r'^[a-zA-Z][\.\)]\s*', '', line)
        return line.strip()

    def is_ingredient_line(self, line: str) -> bool:
        """Check if line looks like an ingredient with improved filtering"""
        debug_line = ("1 cup (160 g) finely diced onions" in line or 
                     "4 cups (940 ml) vegetable broth" in line or 
                     "1 tablespoon (8 g) garlic powder" in line or
                     "2 teaspoons turmeric" in line or
                     "1 cup (96 g) TVP granules" in line or
                     "12 ounces (336 g) uncooked elbow macaroni" in line or
                     "salt and pepper to taste" in line.lower() or
                     "drain the noodles" in line.lower() or
                     "additional seasonings to taste" in line.lower())
        
        if debug_line:
            print(f"    DEBUG LINE: '{line[:50]}...'")
        
        if not line or len(line.strip()) < 3:
            if debug_line:
                print(f"    DEBUG: REJECTED by length check")
            return False
        
        # Clean the line first to remove bullet points and other formatting
        clean_line = self.clean_ingredient_line(line)
        if debug_line:
            print(f"    DEBUG: cleaned line: '{clean_line}'")
            
        if not clean_line or len(clean_line.strip()) < 3:
            if debug_line:
                print(f"    DEBUG: REJECTED by cleaned length check")
            return False
        
        line_lower = clean_line.lower()
        
        # EARLY CHECK: Special case for "to taste" and "salt and pepper" patterns - these are usually ingredients
        # But ONLY if they don't start with instruction verbs AND are short standalone lines
        first_word = line_lower.split()[0] if line_lower.split() else ""
        instruction_starters_early = ['uncover', 'stir', 'mix', 'add', 'heat', 'cook', 'remove', 'serve', 'drain', 'transfer', 'top']
        
        if (re.search(r'\bto\s+taste\b', line_lower) or re.search(r'\bsalt\s+and\s+pepper\b', line_lower)):
            # Only accept as ingredient if it's a short line that doesn't start with instruction verbs
            if first_word not in instruction_starters_early and len(clean_line) < 50:
                if debug_line:
                    print(f"    DEBUG: ACCEPTED early - short 'to taste'/'salt and pepper' line without instruction verb")
                return True
            else:
                if debug_line:
                    print(f"    DEBUG: Contains 'to taste' but starts with instruction verb '{first_word}' or too long ({len(clean_line)} chars) - continuing checks")
        
        # STEP 1: Reject lines that are clearly cooking instructions (start with action verbs)
        instruction_starters = [
            'heat', 'cook', 'bake', 'boil', 'simmer', 'saute', 'fry', 'grill',
            'mix', 'stir', 'whisk', 'blend', 'combine', 'add', 'pour', 'place',
            'remove', 'drain', 'rinse', 'wash', 'chop', 'dice', 'slice', 'cut',
            'preheat', 'serve', 'garnish', 'season', 'taste', 'adjust',
            'make', 'prepare', 'get', 'take', 'put', 'set', 'let', 'allow',
            'bring', 'reduce', 'increase', 'cover', 'uncover', 'flip', 'turn',
            'grate', 'melt', 'dissolve', 'spread', 'brush', 'spray', 'oil',
            'grease', 'line', 'transfer', 'arrange', 'top', 'fill', 'stuff'
        ]
        
        # Check if line starts with an instruction verb (use cleaned line)
        first_word = line_lower.split()[0] if line_lower.split() else ""
        if debug_line:
            print(f"    DEBUG: First word is: '{first_word}'")
        if first_word in instruction_starters:
            if debug_line:
                print(f"    DEBUG: REJECTED by instruction starter: '{first_word}'")
            return False
        
        # STEP 2: Reject numbered instructions (1., 2., Step 1, etc.) - but NOT ingredient quantities
        # Only reject if number is followed by period/parenthesis/dash AND space (like "1. Mix" or "1) Heat" or "1 - Stir")
        # NOT if it's followed by a measurement unit (like "1 cup" or "12 ounces")
        if re.match(r'^\d+[\.\)\-]\s', clean_line) or line_lower.startswith('step'):
            if debug_line:
                print(f"    DEBUG: REJECTED by numbered instruction pattern")
            return False
        
        # STEP 3: Reject section headers
        section_headers = [
            'ingredients', 'directions', 'instructions', 'method', 'preparation',
            'for the', 'herb blend', 'everything else', 'sauce', 'topping',
            'marinade', 'dressing', 'garnish', 'notes', 'variations'
        ]
        
        if any(line_lower.startswith(header) for header in section_headers):
            if debug_line:
                print(f"    DEBUG: REJECTED by section header")
            return False
        
        # STEP 4: Reject lines that are clearly procedural text
        procedural_phrases = [
            'using', 'while', 'until', 'when', 'then', 'next', 'after',
            'before', 'during', 'meanwhile', 'alternately', 'alternatively',
            'if you', 'you can', 'this will', 'this is', 'repeat', 'continue',
            'coming to', 'works pretty', 'is easier', 'my favorite', 'i find', 
            'i like', 'and stir', 'stir them', 'into the sauce'
        ]
        
        # Check for procedural phrases but be more careful about "to taste"
        found_procedural = None
        for phrase in procedural_phrases:
            if phrase in line_lower:
                found_procedural = phrase
                break
        
        if found_procedural:
            if debug_line:
                print(f"    DEBUG: REJECTED by procedural phrase: '{found_procedural}'")
            return False
        
        # STEP 5: Reject very long lines (likely instructions)
        if len(line) > 200:
            if debug_line:
                print(f"    DEBUG: REJECTED by length ({len(line)} chars)")
            return False
        
        if debug_line:
            print(f"    DEBUG: Passed all early rejection checks, testing patterns...")
        
        # STEP 6: Look for positive ingredient indicators
        # Common measurement units
        measurements = [
            'cup', 'cups', 'tablespoon', 'tablespoons', 'tbsp', 'teaspoon', 'teaspoons', 'tsp',
            'pound', 'pounds', 'lb', 'lbs', 'ounce', 'ounces', 'oz', 'gram', 'grams', 'g',
            'kilogram', 'kg', 'liter', 'liters', 'ml', 'milliliter', 'quart', 'pint',
            'gallon', 'inch', 'inches', 'can', 'cans', 'package', 'pkg', 'bottle',
            'jar', 'box', 'bag', 'bunch', 'clove', 'cloves', 'head', 'slice', 'slices'
        ]
        
        # Enhanced pattern matching for ingredients - more comprehensive patterns
        ingredient_patterns = [
            # CRITICAL FIX: Simple whole number + unit patterns (this was missing!)
            r'^\s*\d+\s+(cups?|cup|tablespoons?|tbsp|tablespoon|teaspoons?|tsp|teaspoon)',  # "1 cup", "4 cups", "2 tablespoons"
            r'^\s*\d+\s+(pounds?|lbs?|lb|ounces?|oz|ounce)',  # "1 pound", "12 ounces"
            r'^\s*\d+\s+(grams?|g|kilograms?|kg)',  # "96 g", "1 kg"
            r'^\s*\d+\s+(ml|milliliters?|liters?|l)',  # "235 ml", "1 liter"
            
            # Specific complex patterns with parentheses
            r'^\s*\d+\s*(lbs?|pounds?|lb)\s*\(\s*\d+g?\s*\)',  # "1 lb ( 453g)"
            r'^\s*\d+\s+\d+/\d+\s*-\s*\d+\s*(cups?|cup)\s*\(\d+-\d+\s*ml\)',  # "1 1/3-2 cups (320-473 ml)"
            r'^\s*\d+\s*(cups?|cup)\s*\(\d+\s*g\)',  # "4 cups (400 g)"
            r'^\s*\d+\s*(teaspoons?|tsp)\s*\(\d+\s*g\)',  # "4 teaspoons (8 g)"
            r'^\s*\d+\s*-\s*\d+\s*\(\d+-\d+\s*g\)\s*(tablespoons?|tbsp)',  # "4-7 (15-30 g) tablespoons"
            
            # Decimal patterns
            r'^\s*\d+\.\d+\s*(cups?|tablespoons?|tbsp|tablespoon|teaspoons?|tsp|teaspoon)',
            r'^\s*\d+\.\d+\s*(pounds?|lbs?|lb|ounces?|oz|ounce)',
            r'^\s*\d+\.\d+\s*(grams?|g|kilograms?|kg)',
            r'^\s*\d+\.\d+\s*(ml|milliliters?|liters?|l)',
            
            # Container/package patterns
            r'^\s*\d+\s+(cans?|can|jars?|jar|bottles?|bottle|packages?|pkg|package)',
            r'^\s*\d+\s+(cloves?|clove|heads?|head|bunches?|bunch)',
            r'^\s*\d+\s+(slices?|slice|pieces?|piece)',
            
            # Fraction patterns
            r'^\s*\d+/\d+\s*(cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|lbs?|ounces?|oz)',
            r'^\s*\d+\s+\d+/\d+\s*(cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|lbs?|ounces?|oz)',
            
            # Unicode fractions
            r'^\s*[¼½¾⅓⅔⅛⅜⅝⅞]\s*(cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|lbs?|ounces?|oz)',
            
            # Simple number + any word (catch-all for items like "2 eggs", "3 apples")
            r'^\s*\d+\s+[a-zA-Z]+',  # "2 teaspoons", "12 ounces", etc.
        ]
        
        # Check enhanced patterns first (use cleaned line)
        for i, pattern in enumerate(ingredient_patterns):
            if debug_line:
                print(f"    DEBUG: Testing pattern {i+1}: {pattern}")
                print(f"    DEBUG: Against clean_line: '{clean_line}'")
            if re.match(pattern, clean_line, re.IGNORECASE):
                if debug_line:
                    print(f"    DEBUG: MATCHED ingredient pattern {i+1}: {pattern}")
                return True
            elif debug_line:
                print(f"    DEBUG: Pattern {i+1} did not match")
        
        if debug_line:
            print(f"    DEBUG: No enhanced patterns matched, checking fallback patterns...")
        
        # Check if line contains measurements (strong indicator of ingredient)
        has_measurement = any(re.search(r'^\s*\d+.*?\b' + re.escape(measure) + r'\b', line_lower) or
                             re.search(r'^\s*[¼½¾⅓⅔⅛⅜⅝⅞].*?\b' + re.escape(measure) + r'\b', line_lower)
                             for measure in measurements)
        
        # Check for fraction patterns (1/2, 3/4, etc.) - must be at start of line
        has_fraction = re.search(r'^\s*\d+/\d+', line)
        
        # Check for number at start (quantity indicator) - use cleaned line
        starts_with_number = re.match(r'^\s*\d+', clean_line)
        
        # Must have at least one positive indicator
        if not (has_measurement or has_fraction or starts_with_number):
            if debug_line:
                print(f"    DEBUG: REJECTED - no measurement/fraction/number indicators")
            return False
        
        # STEP 8: Additional checks for common ingredient patterns
        # Reject if it contains too many instruction-like words
        instruction_words = [
            'until', 'then', 'and stir', 'and mix', 'and add', 'and pour',
            'according to', 'as needed', 'or more', 'if needed', 'coming to',
            'works pretty', 'is easier', 'my favorite', 'i find', 'i like'
        ]
        
        instruction_word_count = sum(1 for word in instruction_words if word in line_lower)
        if instruction_word_count > 1:  # More than 1 instruction word = likely instruction
            if debug_line:
                print(f"    DEBUG: REJECTED - too many instruction words ({instruction_word_count})")
            return False
        
        if debug_line:
            print(f"    DEBUG: ACCEPTED as ingredient")
        
        return True

    def is_substitution_note(self, line: str) -> bool:
        """Check if line is a substitution note that should be associated with an ingredient"""
        line_lower = line.lower()
        
        substitution_indicators = [
            'you can replace', 'can replace', 'replace', 'substitute', 
            'try replacing', 'instead of', 'alternative', 'or use',
            'can substitute', 'can use', 'use instead'
        ]
        
        return any(indicator in line_lower for indicator in substitution_indicators)

    def enhance_ingredient_with_substitutions(self, ingredient: str, substitution_notes: List[str]) -> str:
        """Try to match substitution notes with ingredients and append them"""
        ingredient_lower = ingredient.lower()
        
        # Extract key food words from the ingredient
        food_words = ['pecans', 'pecan', 'parsley', 'sage', 'herbs', 'nuts', 'cheese', 
                     'flour', 'oil', 'butter', 'onion', 'garlic', 'milk', 'cream',
                     'mushrooms', 'mushroom', 'chicken', 'beef', 'pork', 'fish']
        
        ingredient_foods = [word for word in food_words if word in ingredient_lower]
        
        for note in substitution_notes:
            note_lower = note.lower()
            
            # Check if this substitution note mentions any of the foods in this ingredient
            if any(food in note_lower for food in ingredient_foods):
                # Clean up the substitution note
                clean_note = note.strip()
                # Remove redundant prefixes
                clean_note = re.sub(r'^(you can |can |try )', '', clean_note, flags=re.IGNORECASE)
                
                # Append to ingredient in parentheses
                return f"{ingredient} ({clean_note})"
        
        return ingredient

    def looks_like_instruction(self, line: str) -> bool:
        """Check if a line looks like a cooking instruction (more strict than is_instruction_line)"""
        line_lower = line.lower()
        
        # If line has strong measurement patterns, it's probably an ingredient, not instruction
        ingredient_patterns = [
            r'\d+\s*(cup|cups|tbsp|tablespoons|tsp|teaspoons|lb|lbs|pound|pounds|oz|ounces|g|grams|kg|ml|liter|liters)\b',
            r'[¼½¾⅓⅔⅛⅜⅝⅞]\s*(cup|cups|tbsp|tablespoons|tsp|teaspoons)',  # Unicode fractions
            r'\d+/\d+\s*(cup|cups|tbsp|tablespoons|tsp|teaspoons)',  # Regular fractions
            r'^\d+\s+[a-zA-Z]',  # Number at start followed by ingredient
        ]
        
        if any(re.search(pattern, line_lower) for pattern in ingredient_patterns):
            return False
        
        # Strong instruction verbs that indicate cooking actions (not ingredient prep)
        strong_instruction_verbs = [
            'preheat', 'heat the', 'cook the', 'bake for', 'boil for', 'simmer for', 
            'sauté until', 'fry until', 'mix together', 'stir in', 'whisk until', 
            'beat until', 'fold in', 'combine all', 'add to', 'pour into',
            'place in', 'put in', 'set aside', 'remove from', 'take out', 
            'serve with', 'garnish with', 'season with', 'taste and', 
            'adjust', 'cover and', 'uncover', 'drain and'
        ]
        
        # Only trigger on strong instruction phrases, not simple prep words
        if any(verb in line_lower for verb in strong_instruction_verbs) and len(line) > 15:
            return True
        
        # Sequential indicators (step 1, first, then, next, etc.)
        if re.search(r'\b(step\s+\d+|first|then|next|meanwhile|after|before|until|when)\b', line_lower):
            return True
        
        # Temperature/time references (usually instructions)
        if re.search(r'\b\d+\s*(degrees?|°|minutes?|hours?|mins?|hrs?)\b', line_lower):
            return True
        
        return False

    def extract_instructions(self, content: str, recipe_title: str = "Unknown Recipe") -> List[str]:
        """Extract cooking instructions with inline images"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        instructions = []
        
        print(f"\n{'*'*80}")
        print(f"* INSTRUCTION EXTRACTION - {recipe_title}")
        print(f"{'*'*80}")
        print(f"Total lines to process: {len(lines)}")
        
        # Get all substitution notes to exclude them from instructions
        substitution_notes = []
        for line in lines:
            if self.is_substitution_note(line):
                substitution_notes.append(line.lower())
        
        # Look for instruction patterns and include image placeholders
        for line in lines:
            # Debug the problematic lines in instructions too
            debug_line = ("grate your cheese" in line.lower() or 
                         "drain the noodles" in line.lower() or
                         "remove from heat" in line.lower() or
                         "allow to stand" in line.lower())
            if debug_line:
                print(f"\n    DEBUG instruction check for: '{line[:80]}...'")
                print(f"    DEBUG: is_instruction_line = {self.is_instruction_line(line)}")
                print(f"    DEBUG: is_ingredient_line = {self.is_ingredient_line(line)}")
                print(f"    DEBUG: len(line) = {len(line)}")
            
            # Skip URLs completely
            if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                continue
                
            # Skip page numbers and references
            if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                if debug_line:
                    print(f"    DEBUG: SKIPPED - contains page reference")
                continue
                
            # Skip ONLY standalone time information like "Prep time: 15 minutes" but NOT cooking instructions with time
            if re.search(r'\b(prep|cook|total)\s+time\b', line.lower()):
                if debug_line:
                    print(f"    DEBUG: SKIPPED - contains standalone time info")
                continue
                
            # ALLOW yield/serving info in instructions (keep it as useful recipe metadata)
            # Skip serving/yield info
            # if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
            #     continue
            
            # Skip substitution notes that are already in ingredients
            if any(note in line.lower() for note in substitution_notes):
                continue
            
            # Check if this is an image placeholder
            if re.match(r'\[IMAGE_\d+\]', line):
                instructions.append(line)  # Keep image placeholders as separate instructions
                if debug_line:
                    print(f"    DEBUG: ADDED as IMAGE placeholder")
            elif (self.is_instruction_line(line) and 
                  len(line) > 15 and 
                  not self.is_ingredient_line(line)):  # Make sure it's not also an ingredient
                clean_line = self.clean_instruction_line(line)
                if debug_line:
                    print(f"    DEBUG: clean_instruction_line returned: '{clean_line}'")
                if clean_line:
                    instructions.append(clean_line)
                    if debug_line:
                        print(f"    DEBUG: ADDED as INSTRUCTION: '{clean_line}'")
                else:
                    if debug_line:
                        print(f"    DEBUG: REJECTED - clean_instruction_line returned empty/None")
            else:
                if debug_line:
                    is_instruction = self.is_instruction_line(line)
                    is_ingredient = self.is_ingredient_line(line)
                    print(f"    DEBUG: REJECTED instruction - is_instruction={is_instruction}, is_ingredient={is_ingredient}, len={len(line)}")
                    if is_instruction and is_ingredient:
                        print(f"    DEBUG: Line classified as BOTH instruction and ingredient - rejecting as instruction")
        
        print(f"=== FINAL INSTRUCTION COUNT: {len(instructions)} ===")
        for i, inst in enumerate(instructions):
            print(f"  {i+1}. {inst[:100]}...")
        print("=" * 50)
        print(f"* END INSTRUCTION EXTRACTION - {recipe_title}")
        print(f"{'*'*80}")
        
        # Fallback: use longer lines that aren't ingredients
        if not instructions:
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
        # Debug for the specific missing line
        debug_this_line = "remove from heat" in line.lower() and "allow to stand" in line.lower()
        if debug_this_line:
            print(f"\n      DEBUG clean_instruction_line input: '{line}'")
        
        # Remove bullet points and checkmarks
        line = re.sub(r'^[•\-\*☐✓]\s*', '', line)
        # Remove leading numbers with periods/parentheses
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        
        result = line.strip()
        if debug_this_line:
            print(f"      DEBUG clean_instruction_line output: '{result}'")
        
        return result

    def is_instruction_line(self, line: str) -> bool:
        """Check if line looks like an instruction"""
        line_lower = line.lower()
        
        # Debug for the specific missing line
        debug_this_line = "remove from heat" in line_lower and "allow to stand" in line_lower
        if debug_this_line:
            print(f"\n      DEBUG is_instruction_line for: '{line[:50]}...'")
        
        # Exclude serving/yield info first
        if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line_lower):
            if debug_this_line:
                print(f"      DEBUG: REJECTED - contains yield/serving info")
            return False
        
        # Exclude time information (but not cooking instructions that mention time)
        # Only reject standalone time references like "Prep time: 15 minutes" or "Cook time: 30 min"
        if re.search(r'\b(prep|cook|total)\s+time\b', line_lower):
            if debug_this_line:
                print(f"      DEBUG: REJECTED - contains time info")
            return False
        
        # Must be longer than typical ingredients
        if len(line) < 20:
            if debug_this_line:
                print(f"      DEBUG: REJECTED - too short ({len(line)} chars)")
            return False
        
        instruction_keywords = [
            'cook', 'bake', 'mix', 'add', 'heat', 'stir', 'pour', 'place', 
            'remove', 'serve', 'prepare', 'combine', 'season', 'boil', 
            'simmer', 'fry', 'chop', 'slice', 'dice', 'mince', 'whisk',
            'blend', 'fold', 'beat', 'knead', 'roll', 'spread', 'brush',
            'drizzle', 'sprinkle', 'garnish', 'chill', 'freeze', 'thaw',
            'create','preheat', 'until', 'then', 'next', 'meanwhile'
        ]
        
        # Use word boundaries to match complete words only
        has_instruction_keyword = any(re.search(r'\b' + re.escape(keyword) + r'\b', line_lower) for keyword in instruction_keywords)
        
        if debug_this_line:
            print(f"      DEBUG: has_instruction_keyword = {has_instruction_keyword}")
            matching_keywords = [kw for kw in instruction_keywords if re.search(r'\b' + re.escape(kw) + r'\b', line_lower)]
            print(f"      DEBUG: matching keywords = {matching_keywords}")
        
        return has_instruction_keyword

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

    def post_process_ingredients_from_instructions(self, ingredients: List[str], instructions: List[str], recipe_title: str = "Unknown Recipe") -> tuple[List[str], List[str]]:
        """Post-process instructions to find misclassified ingredients and move them back"""
        print(f"\n{'~'*80}")
        print(f"~ POST-PROCESSING INGREDIENTS FROM INSTRUCTIONS - {recipe_title}")
        print(f"{'~'*80}")
        
        new_ingredients = ingredients.copy()
        new_instructions = []
        moved_count = 0
        
        for i, instruction in enumerate(instructions):
            # Skip image placeholders
            if re.match(r'\[IMAGE_\d+\]', instruction):
                new_instructions.append(instruction)
                continue
            
            instruction_lower = instruction.lower()
            
            # Debug the specific problematic line
            if "optional additional seasonings" in instruction_lower:
                print(f"  DEBUG: Found target line: '{instruction}'")
                print(f"  DEBUG: instruction_lower: '{instruction_lower}'")
            
            # Check for patterns that indicate this is actually ingredient information
            ingredient_indicators = [
                # Very specific pattern for the exact line we want to catch
                r'optional\s+additional\s+seasonings?\s+to\s+taste.*i\s+usually\s+add',
                # More flexible pattern for optional seasonings
                r'^optional\s+additional\s+seasonings?\s+to\s+taste',
                # Standalone "to taste" without cooking verbs
                r'^\s*to\s+taste\s*[-:]?\s*(salt|pepper|seasoning)',
                # Simple ingredient lists starting with "optional"
                r'^optional\s*[-:]?\s*[a-z\s,&]+\s+to\s+taste\s*$',
            ]
            
            is_likely_ingredient = False
            matched_pattern = None
            
            # Safety check: Don't move lines that contain cooking verbs (actual instructions)
            cooking_verbs = [
                'make', 'melt', 'mix', 'stir', 'cook', 'heat', 'drain', 'transfer', 
                'add in', 'dump', 'brown', 'mixing in', 'stirring', 'top with',
                'melting', 'breadcrumb', 'topping', 'constantly', 'drain and',
                'noodles', 'transfer to', 'plate'
            ]
            
            has_cooking_verbs = any(verb in instruction_lower for verb in cooking_verbs)
            
            if not has_cooking_verbs:  # Only consider if no cooking verbs present
                for j, pattern in enumerate(ingredient_indicators):
                    if "optional additional seasonings" in instruction_lower:
                        print(f"    DEBUG: Testing pattern {j+1}: {pattern}")
                    if re.search(pattern, instruction_lower):
                        if "optional additional seasonings" in instruction_lower:
                            print(f"    DEBUG: MATCHED pattern {j+1}!")
                        is_likely_ingredient = True
                        matched_pattern = pattern
                        break
                    elif "optional additional seasonings" in instruction_lower:
                        print(f"    DEBUG: No match for pattern {j+1}")
            elif "optional additional seasonings" in instruction_lower:
                print(f"    DEBUG: Skipped due to cooking verbs: {[verb for verb in cooking_verbs if verb in instruction_lower]}")
            
            if is_likely_ingredient:
                print(f"  MOVING TO INGREDIENTS: '{instruction[:80]}...'")
                print(f"    Matched pattern: {matched_pattern}")
                
                # Clean up the instruction to make it more ingredient-like
                clean_ingredient = instruction
                
                # Tidy up dashes and spacing
                clean_ingredient = re.sub(r'\s+', ' ', clean_ingredient).strip()
                
                new_ingredients.append(clean_ingredient)
                moved_count += 1
                print(f"    CLEANED TO: '{clean_ingredient}'")
            else:
                new_instructions.append(instruction)
        
        print(f"  MOVED {moved_count} items from instructions to ingredients")
        print(f"~ END POST-PROCESSING - {recipe_title}")
        print(f"{'~'*80}")
        
        return new_ingredients, new_instructions


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
