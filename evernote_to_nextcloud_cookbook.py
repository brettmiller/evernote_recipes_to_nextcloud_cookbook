#!/usr/bin/env python3
"""
Evernote .enex to Nextcloud Recipes Export Converter

This script converts Evernote .enex files to Nextcloud Recipes export format.
Creates individual JSON files for each recipe using Recipe schema.

When source URLs are found in the notes, the script will attempt to fetch
fresh content from the original recipe websites for better accuracy.

Requirements:
- requests library: pip install requests
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
import requests
from urllib.parse import urljoin, urlparse
import time


class EvernoteToNextcloudConverter:
    def __init__(self, input_dir: str, output_file: str, debug: bool = False):
        self.input_dir = Path(input_dir)
        self.output_file = Path(output_file)
        if not self.output_file.suffix:
            self.output_file = self.output_file.with_suffix('.zip')
        
        # Create temporary directory
        self.temp_dir = Path(tempfile.mkdtemp())
        self.recipe_counter = 0
        self.debug = debug
        self.enable_web_fetch = True  # Enabled by default, try curl-like approach first

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
            content = content_elem.text if content_elem.text is not None else ""
            created = created_elem.text if created_elem is not None else None
            
            if self.debug:
                print(f"\n{'='*80}")
                print(f"PROCESSING RECIPE: {title}")
                print(f"{'='*80}")
            
            # First, try to extract source URL from content
            source_url = self.extract_source_url(content, title)
            
            # Try to fetch fresh content from URL
            web_content = None
            if source_url and self.enable_web_fetch:
                web_content = self.fetch_recipe_from_url(source_url)
                if web_content:
                    if self.debug:
                        print(f"    Using web content from: {source_url}")
                    # Use web content instead of Evernote content
                    text_content = web_content
                    images = []  # Web content won't have embedded images
                else:
                    if self.debug:
                        print(f"    Failed to fetch web content, using Evernote content")
                    # Fall back to Evernote content
                    text_content, images = self.parse_content_and_images(content, note)
            else:
                if self.debug:
                    print(f"    No source URL found, using Evernote content")
                # Parse content and extract images from Evernote
                text_content, images = self.parse_content_and_images(content, note)
            
            # Add small delay to be respectful to websites
            if source_url and web_content:
                time.sleep(1)
            ingredients = self.extract_ingredients(text_content, title)
            instructions = self.extract_instructions(text_content, title)
            description = self.extract_description(text_content)
            
            # Use the source_url we already extracted
            final_source_url = source_url or ""
            
            # Post-process instructions to move misclassified ingredients back to ingredients list
            final_ingredients, final_instructions = self.post_process_ingredients_from_instructions(ingredients, instructions, title)
            
            if self.debug:
                print(f"\n{'='*80}")
                print(f"FINISHED PROCESSING: {title}")
                print(f"  - Ingredients: {len(final_ingredients)}")
                print(f"  - Instructions: {len(final_instructions)}")
                print(f"  - Images: {len(images)}")
                if final_source_url and web_content:
                    print(f"  - Content source: Web (fetched from {final_source_url})")
                elif final_source_url:
                    print(f"  - Content source: Evernote (web fetch failed for {final_source_url})")
                else:
                    print(f"  - Content source: Evernote (no URL found)")
                print(f"{'='*80}\n")
            
            # Create recipe data without image filenames first
            self.recipe_counter += 1
            recipe_data = self.create_recipe_data(
                self.recipe_counter, title, description, 
                final_ingredients, final_instructions, created, [], final_source_url
            )
            
            # Create recipe directory with images
            return self.create_recipe_dir(self.recipe_counter, recipe_data, title, images, note, web_content)
            
        except Exception as e:
            print(f"Error processing note: {e}")
            return None

    def create_recipe_data(self, recipe_id: int, name: str, description: str,
                          ingredients: List[str], instructions: List[str], 
                          created: Optional[str], image_files: Optional[List[str]] = None, 
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

    def create_recipe_dir(self, recipe_id: int, recipe_data: Dict, title: str, images: List[Dict], note: ET.Element, web_content: Optional[str] = None) -> Path:
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
                if self.debug:
                    print(f"    Saved image: {image_filename}")
                
            except Exception as e:
                print(f"    Error saving image: {e}")
        
        # Update recipe data with actual image filenames and regenerate instructions
        # Get the text content again to extract original instructions with placeholders
        if web_content:
            # If we used web content, use that for regenerating instructions
            original_instructions = self.extract_instructions(web_content, recipe_data["name"])
        else:
            # Otherwise, re-parse the Evernote content
            title_elem = note.find('title')
            content_elem = note.find('content')
            content = content_elem.text if content_elem is not None and content_elem.text is not None else ""
            
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
        
        # Show processing result with URL info
        url_info = ""
        if recipe_data.get("url") and web_content:
            url_info = f" (fetched from {recipe_data['url']})"
        elif recipe_data.get("url"):
            url_info = f" (web fetch failed for {recipe_data['url']})"
        
        print(f"  Recipe {recipe_id}: {title} ({len(image_filenames)} images){url_info}")
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
                        if not resource_hash and data_elem.text:
                            resource_hash = hashlib.md5(data_elem.text.encode()).hexdigest()
                        
                        # Skip if we still don't have a hash
                        if not resource_hash:
                            continue
                        
                        if self.debug:
                            print(f"    Found image with hash: {resource_hash[:8]}...")
                        
                        # Decode base64 image data
                        if data_elem.text:
                            image_data = base64.b64decode(data_elem.text)
                        else:
                            continue  # Skip if no image data
                        
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
            if self.debug:
                print(f"    Found en-media tag with hash: {hash_attr[:8]}...")
            if hash_attr in image_hash_to_data:
                image_index = image_hash_to_data[hash_attr]
                if self.debug:
                    print(f"    Replacing with IMAGE_{image_index}")
                return f"\n[IMAGE_{image_index}]\n"
            else:
                if self.debug:
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

    def fetch_recipe_from_url(self, url: str) -> Optional[str]:
        """Fetch and parse recipe content from URL with multiple fallback strategies"""
        if not url or not url.startswith(('http://', 'https://')):
            return None
        
        # Clean up URL - remove anchors and unnecessary parameters
        clean_url = self.clean_recipe_url(url)
        
        if self.debug:
            print(f"    Fetching recipe from URL: {url}")
            if clean_url != url:
                print(f"    Cleaned URL: {clean_url}")
        
        # Quick connectivity test
        try:
            import socket
            host = urlparse(clean_url).netloc
            if self.debug:
                print(f"    Testing connectivity to {host}...")
            
            # Test basic DNS resolution and connectivity
            socket.create_connection((host, 443 if clean_url.startswith('https') else 80), timeout=5)
            if self.debug:
                print(f"    Connectivity test passed")
        except Exception as e:
            if self.debug:
                print(f"    Connectivity test failed: {e}")
            return None
        
        # Quick HTTP test to verify we can actually make requests
        try:
            if self.debug:
                print(f"    Testing basic HTTP request...")
            test_response = requests.get(clean_url, timeout=5, allow_redirects=False)
            if self.debug:
                print(f"    Basic HTTP test result: {test_response.status_code}")
        except Exception as e:
            if self.debug:
                print(f"    Basic HTTP test failed: {e}")
            # Don't return None here, still try the strategies
        
        # Try multiple strategies in order of preference
        # Start with general strategies, then use enhanced methods as fallbacks for any difficult site
        strategies = [
            self._fetch_with_simple_headers,
            self._fetch_with_curl_headers,
            self._fetch_with_basic_requests,
            self._fetch_with_modern_browser,
            self._fetch_with_minimal_headers,
            self._fetch_with_requests_session,  # Ultra-lenient fetch method
            # Enhanced methods as fallbacks for difficult sites
            self._fetch_with_chrome_headers,
            self._fetch_with_safari_headers,
            # Additional strategies for very stubborn sites
            self._fetch_with_firefox_headers,
            self._fetch_with_edge_headers,
            self._fetch_with_extended_timeout,  # Longer timeouts and delays
            self._fetch_with_no_ssl_verification,  # Last resort for SSL issues
        ]
        
        for i, strategy in enumerate(strategies, 1):
            if self.debug:
                strategy_name = strategy.__name__.replace('_fetch_with_', '').replace('_', ' ')
                print(f"    Trying strategy {i} ({strategy_name})...")
            
            try:
                result = strategy(clean_url)
                if result:
                    if self.debug:
                        print(f"    Strategy {i} succeeded!")
                    return result
                else:
                    if self.debug:
                        print(f"    Strategy {i} returned empty result")
            except requests.exceptions.Timeout as e:
                if self.debug:
                    print(f"    Strategy {i} timed out: {e}")
                # For major sites, add a delay before trying next strategy
                if any(site in clean_url.lower() for site in ['seriouseats.com', 'nytimes.com']):
                    time.sleep(1)
                continue
            except requests.exceptions.HTTPError as e:
                if self.debug:
                    status_code = getattr(e.response, 'status_code', 'unknown') if hasattr(e, 'response') else 'unknown'
                    print(f"    Strategy {i} HTTP error: {e} (status: {status_code})")
                # For 403/429 errors on major sites, add delay
                if hasattr(e, 'response') and e.response.status_code in [403, 429]:
                    if any(site in clean_url.lower() for site in ['seriouseats.com', 'nytimes.com']):
                        if self.debug:
                            print(f"    Adding delay for {e.response.status_code} error on major site")
                        time.sleep(2)
                continue
            except Exception as e:
                if self.debug:
                    print(f"    Strategy {i} failed with exception: {e}")
                continue
        
        if self.debug:
            print(f"    All strategies failed for URL")
        return None
    
    def _fetch_with_requests_session(self, url: str) -> Optional[str]:
        """Ultra-simple fetch with just requests.Session() - no custom headers at all"""
        try:
            if self.debug:
                print(f"      Making bare session request to: {url} (SSL verification: ON)")
            
            with requests.Session() as session:
                # Add minimal delay to be respectful
                time.sleep(0.5)
                
                response = session.get(url, timeout=20, verify=True)  # Try SSL verification first
                
                if self.debug:
                    print(f"      Got response: {response.status_code}")
                    print(f"      Content length: {len(response.text)}")
                
                # Be very lenient with status codes
                if response.status_code in [200, 301, 302, 304]:
                    # Try to process even if it's not perfect
                    return self._process_response_lenient(response)
                else:
                    response.raise_for_status()
                    return self._process_response_lenient(response)
                    
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Requests session - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled...")
            # Try without SSL verification as fallback
            try:
                with requests.Session() as session:
                    time.sleep(0.5)
                    response = session.get(url, timeout=20, verify=False)
                    if self.debug:
                        print(f"      Got response (no SSL): {response.status_code}")
                    
                    if response.status_code in [200, 301, 302, 304]:
                        return self._process_response_lenient(response)
                    else:
                        response.raise_for_status()
                        return self._process_response_lenient(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Requests session - SSL fallback also failed: {e2}")
                raise e
        except Exception as e:
            if self.debug:
                print(f"      Requests session - ERROR: {type(e).__name__}: {e}")
            raise
    
    def _process_response_lenient(self, response) -> Optional[str]:
        """Process response with very lenient validation for difficult sites"""
        if self.debug:
            print(f"      Lenient processing - status: {response.status_code}, length: {len(response.text)}")
        
        # Very minimal validation - just check we got some content
        if len(response.text) < 20:
            if self.debug:
                print(f"      Response too short for lenient processing")
            return None
        
        # Skip most validation for major recipe sites - just try to extract content
        if any(site in response.url.lower() for site in ['seriouseats.com', 'nytimes.com', 'foodnetwork.com', 'allrecipes.com']):
            if self.debug:
                print(f"      Major recipe site - skipping most validation")
            
            # Try to extract recipe content directly without strict validation
            recipe_content = self.extract_recipe_from_html(response.text)
            
            if recipe_content and len(recipe_content.strip()) > 20:
                if self.debug:
                    print(f"      Lenient extraction successful: {len(recipe_content)} characters")
                return recipe_content
            else:
                # If recipe extraction fails, return raw HTML-to-text conversion
                raw_text = self.html_to_text(response.text)
                if len(raw_text.strip()) > 100:
                    if self.debug:
                        print(f"      Fallback to raw text conversion: {len(raw_text)} characters")
                    return raw_text[:2000]  # Limit to reasonable size
        
        # Special handling for ediblecommunities.com sites - be very lenient
        if 'ediblecommunities.com' in response.url.lower():
            if self.debug:
                print(f"      Edible communities site - using ultra-lenient processing")
            
            # Accept any response with substantial content, even with error codes
            if len(response.text) > 500:
                # Try recipe extraction first
                recipe_content = self.extract_recipe_from_html(response.text)
                
                if recipe_content and len(recipe_content.strip()) > 50:
                    if self.debug:
                        print(f"      Edible site recipe extraction successful: {len(recipe_content)} characters")
                    return recipe_content
                else:
                    # Fall back to raw text conversion for edible sites
                    raw_text = self.html_to_text(response.text)
                    if len(raw_text.strip()) > 200:
                        if self.debug:
                            print(f"      Edible site raw text fallback: {len(raw_text)} characters")
                        return raw_text[:3000]  # Larger limit for these sites
            
            # Show some debug info about what we got
            if self.debug:
                print(f"      Edible site response preview: {response.text[:500]}...")
        
        # For other sites, use normal processing
        return self._process_response(response)

    def _fetch_with_extended_timeout(self, url: str) -> Optional[str]:
        """Fetch with extended timeouts and delays for very slow/stubborn sites"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://www.google.com/',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = False  # Disable SSL for stubborn sites
            
            if self.debug:
                print(f"      Making extended timeout request to: {url}")
            
            # Add extended delay for very slow sites
            time.sleep(5)
            
            response = session.get(url, timeout=60, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
                print(f"      Content length: {len(response.text)}")
            
            # Be very lenient with status codes
            if response.status_code in [200, 301, 302, 304, 403, 429] and len(response.text) > 100:
                return self._process_response_lenient(response)
            else:
                response.raise_for_status()
                return self._process_response_lenient(response)
                
        except Exception as e:
            if self.debug:
                print(f"      Extended timeout - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_no_ssl_verification(self, url: str) -> Optional[str]:
        """Last resort fetch with no SSL verification and very permissive settings"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Referer': 'https://www.google.com/',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = False  # No SSL verification at all
            
            if self.debug:
                print(f"      Making no-SSL request to: {url}")
            
            # Extended delay and timeout for last resort
            time.sleep(7)
            
            response = session.get(url, timeout=90, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
                print(f"      Content length: {len(response.text)}")
                print(f"      Headers: {dict(list(response.headers.items())[:5])}")
            
            # Accept any response that has substantial content
            if response.status_code in [200, 301, 302, 304, 403, 429, 500, 503] and len(response.text) > 100:
                return self._process_response_lenient(response)
            else:
                if self.debug:
                    print(f"      Unexpected status or empty response")
                response.raise_for_status()
                return self._process_response_lenient(response)
                
        except Exception as e:
            if self.debug:
                print(f"      No SSL verification - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()

    def clean_recipe_url(self, url: str) -> str:
        """Clean up recipe URLs by removing unwanted path segments and parameters"""
        if not url:
            return url
        
        # Remove fragments first (everything after #)
        url = url.split('#')[0]
        
        # Remove common unwanted path segments
        unwanted_segments = [
            '/print',
            '/print/',
            '/amp',
            '/amp/',
            '/mobile',
            '/mobile/',
            '/comments',
            '/comments/',
            '/comment',
            '/comment/',
            '/respond',
            '/respond/',
            '/feed',
            '/feed/',
            '/rss',
            '/rss/',
            '/trackback',
            '/trackback/',
        ]
        
        # Remove unwanted segments and everything after them
        for segment in unwanted_segments:
            if segment in url:
                # Find the position and truncate
                pos = url.find(segment)
                if pos > 0:
                    # Only truncate if the segment isn't part of the main path
                    # Check if there's a meaningful path before the unwanted segment
                    before_segment = url[:pos]
                    if before_segment.rstrip('/').count('/') >= 3:  # Has at least https://domain.com/path
                        url = before_segment.rstrip('/')
                        if self.debug:
                            print(f"      Removed segment '{segment}' from URL")
                        break
        
        # Remove unwanted query parameters but keep others
        if '?' in url:
            base_url, query_string = url.split('?', 1)
            
            # Parse query parameters
            params = []
            for param in query_string.split('&'):
                if '=' in param:
                    key, value = param.split('=', 1)
                    # Skip tracking and unwanted parameters
                    unwanted_params = [
                        'utm_', 'ref', 'src', 'fbclid', 'gclid', 'mc_', 'campaign',
                        'medium', 'source', 'content', 'term', 'cid', 'sid',
                        'print', 'share', 'comment', 'respond'
                    ]
                    
                    # Keep parameter only if it doesn't start with unwanted prefixes
                    if not any(key.lower().startswith(unwanted) for unwanted in unwanted_params):
                        params.append(param)
            
            # Rebuild URL with clean parameters
            if params:
                url = base_url + '?' + '&'.join(params)
            else:
                url = base_url
        
        # Remove only trailing semicolons and /; combinations (don't touch other punctuation)
        original_url = url
        
        # Loop through and remove trailing ; and / until none remain
        while url.endswith(';') or url.endswith('/'):
            if url.endswith(';'):
                url = url[:-1]  # Remove trailing semicolon
            if url.endswith('/'):
                url = url[:-1]  # Remove trailing slash
        
        # Debug output to see what's happening
        if self.debug and original_url != url:
            print(f"      URL cleaned: '{original_url}' -> '{url}'")
        
        return url

    def _fetch_with_modern_browser(self, url: str) -> Optional[str]:
        """Fetch with full modern browser headers"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = True  # Enable SSL verification by default
            
            if self.debug:
                print(f"      Making modern browser request to: {url} (SSL verification: ON)")
            
            response = session.get(url, timeout=15, allow_redirects=True)
            
            if response.status_code in [403, 429, 503]:
                # Try with referer
                headers['Referer'] = 'https://www.google.com/'
                session.headers.update(headers)
                time.sleep(1)
                response = session.get(url, timeout=15, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            response.raise_for_status()
            return self._process_response(response)
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Modern browser - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled...")
            # Try without SSL verification as fallback
            try:
                if session:
                    session.verify = False
                    response = session.get(url, timeout=15, allow_redirects=True)
                    
                    if response.status_code in [403, 429, 503]:
                        # Try with referer
                        headers['Referer'] = 'https://www.google.com/'
                        session.headers.update(headers)
                        time.sleep(1)
                        response = session.get(url, timeout=15, allow_redirects=True)
                    
                    if self.debug:
                        print(f"      Got response (no SSL): {response.status_code}")
                    response.raise_for_status()
                    return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Modern browser - SSL fallback also failed: {e2}")
                raise e
        except requests.exceptions.Timeout as e:
            if self.debug:
                print(f"      Modern browser - TIMEOUT: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            if self.debug:
                print(f"      Modern browser - CONNECTION ERROR: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            if self.debug:
                status_code = getattr(e.response, 'status_code', 'unknown') if hasattr(e, 'response') else 'unknown'
                print(f"      Modern browser - HTTP ERROR: {e} (status: {status_code})")
            raise
        except Exception as e:
            if self.debug:
                print(f"      Modern browser - OTHER ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_simple_headers(self, url: str) -> Optional[str]:
        """Fetch with minimal, less suspicious headers"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        
        session = None
        try:
            # Use session for better connection handling
            session = requests.Session()
            session.headers.update(headers)
            
            # Start with SSL verification enabled (secure by default)
            session.verify = True
            session.max_redirects = 3
            
            if self.debug:
                print(f"      Making request to: {url} (SSL verification: ON)")
            
            # Try with a shorter timeout first
            response = session.get(url, timeout=10, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            
            response.raise_for_status()
            return self._process_response(response)
            
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Simple headers - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled...")
            # Try without SSL verification as fallback
            try:
                if session:
                    session.verify = False
                    response = session.get(url, timeout=10, allow_redirects=True)
                    if self.debug:
                        print(f"      Got response (no SSL): {response.status_code}")
                    response.raise_for_status()
                    return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Simple headers - SSL fallback also failed: {e2}")
                raise e
        except requests.exceptions.Timeout as e:
            if self.debug:
                print(f"      Simple headers - TIMEOUT: {e}")
            # Try one more time with longer timeout
            try:
                if session:
                    response = session.get(url, timeout=30, allow_redirects=True)
                    response.raise_for_status()
                    return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Simple headers - Second attempt failed: {e2}")
                raise e
        except requests.exceptions.ConnectionError as e:
            if self.debug:
                print(f"      Simple headers - CONNECTION ERROR: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            if self.debug:
                status_code = getattr(e.response, 'status_code', 'unknown') if hasattr(e, 'response') else 'unknown'
                print(f"      Simple headers - HTTP ERROR: {e} (status: {status_code})")
            raise
        except Exception as e:
            if self.debug:
                print(f"      Simple headers - OTHER ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_curl_headers(self, url: str) -> Optional[str]:
        """Fetch with curl-like headers (often works better than browser simulation)"""
        headers = {
            'User-Agent': 'curl/8.4.0',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = True  # Enable SSL verification by default
            
            if self.debug:
                print(f"      Making curl-style request to: {url} (SSL verification: ON)")
            
            response = session.get(url, timeout=15, allow_redirects=True)
            if self.debug:
                print(f"      Got response: {response.status_code}")
            response.raise_for_status()
            return self._process_response(response)
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Curl headers - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled...")
            # Try without SSL verification as fallback
            try:
                if session:
                    session.verify = False
                    response = session.get(url, timeout=15, allow_redirects=True)
                    if self.debug:
                        print(f"      Got response (no SSL): {response.status_code}")
                    response.raise_for_status()
                    return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Curl headers - SSL fallback also failed: {e2}")
                raise e
        except requests.exceptions.Timeout as e:
            if self.debug:
                print(f"      Curl headers - TIMEOUT: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            if self.debug:
                print(f"      Curl headers - CONNECTION ERROR: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            if self.debug:
                status_code = getattr(e.response, 'status_code', 'unknown') if hasattr(e, 'response') else 'unknown'
                print(f"      Curl headers - HTTP ERROR: {e} (status: {status_code})")
            raise
        except Exception as e:
            if self.debug:
                print(f"      Curl headers - OTHER ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_basic_requests(self, url: str) -> Optional[str]:
        """Ultra-simple fetch with no special headers or session management"""
        try:
            if self.debug:
                print(f"      Making basic request to: {url} (SSL verification: ON)")
            
            # Most basic request possible - no custom headers, but with SSL verification first
            response = requests.get(url, timeout=15, verify=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            
            response.raise_for_status()
            return self._process_response(response)
            
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Basic requests - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled...")
            # Try without SSL verification as fallback
            try:
                response = requests.get(url, timeout=15, verify=False)
                if self.debug:
                    print(f"      Got response (no SSL): {response.status_code}")
                response.raise_for_status()
                return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Basic requests - SSL fallback also failed: {e2}")
                raise e
        except requests.exceptions.Timeout as e:
            if self.debug:
                print(f"      Basic requests - TIMEOUT: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            if self.debug:
                print(f"      Basic requests - CONNECTION ERROR: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            if self.debug:
                status_code = getattr(e.response, 'status_code', 'unknown') if hasattr(e, 'response') else 'unknown'
                print(f"      Basic requests - HTTP ERROR: {e} (status: {status_code})")
            raise
        except Exception as e:
            if self.debug:
                print(f"      Basic requests - OTHER ERROR: {type(e).__name__}: {e}")
            raise
    
    def _fetch_with_minimal_headers(self, url: str) -> Optional[str]:
        """Fetch with absolute minimal headers (most permissive)"""
        headers = {
            'User-Agent': 'python-requests/2.31.0',
            'Accept': 'text/html,application/xhtml+xml,*/*',
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            if self.debug:
                print(f"      Got response: {response.status_code}")
            response.raise_for_status()
            return self._process_response(response)
        except requests.exceptions.Timeout as e:
            if self.debug:
                print(f"      Minimal headers - TIMEOUT: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            if self.debug:
                print(f"      Minimal headers - CONNECTION ERROR: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            if self.debug:
                status_code = getattr(e.response, 'status_code', 'unknown') if hasattr(e, 'response') else 'unknown'
                print(f"      Minimal headers - HTTP ERROR: {e} (status: {status_code})")
            raise
        except Exception as e:
            if self.debug:
                print(f"      Minimal headers - OTHER ERROR: {type(e).__name__}: {e}")
            raise
    
    def _process_response(self, response) -> Optional[str]:
        """Process HTTP response and extract recipe content"""
        if self.debug:
            print(f"      Response status: {response.status_code}")
            print(f"      Content length: {len(response.text)} chars")
            print(f"      Content-Type: {response.headers.get('content-type', 'unknown')}")
        
        # Check if we got a reasonable response - be more lenient for major sites
        min_content_length = 100
        if any(site in response.url.lower() for site in ['seriouseats.com', 'nytimes.com', 'foodnetwork.com', 'allrecipes.com']):
            min_content_length = 50  # More lenient for major recipe sites
        
        if len(response.text) < min_content_length:
            if self.debug:
                print(f"      Response too short ({len(response.text)} chars, minimum: {min_content_length})")
            return None
        
        # Check content type - be more lenient
        content_type = response.headers.get('content-type', '').lower()
        if content_type and not any(ct in content_type for ct in ['text/html', 'application/xhtml', 'text/plain']):
            if self.debug:
                print(f"      Invalid content type: {content_type}")
            return None
        
        # Check for common blocking indicators - be very conservative
        response_text_lower = response.text.lower()
        blocking_indicators = [
            'access denied',
            'forbidden',
            'you are being blocked',
            'bot detected',
            'security check required',
            'rate limit exceeded',
            'temporarily unavailable',
        ]
        
        # Only check first 1000 characters for blocking indicators
        page_start = response_text_lower[:1000]
        found_blocking = None
        for indicator in blocking_indicators:
            if indicator in page_start:
                found_blocking = indicator
                break
        
        if found_blocking:
            if self.debug:
                print(f"      Response contains blocking indicator: {found_blocking}")
            # For major recipe sites, be very lenient - proceed if we have any substantial content
            if any(site in response.url.lower() for site in ['seriouseats.com', 'nytimes.com', 'foodnetwork.com', 'allrecipes.com']):
                if len(response.text) > 1000:
                    if self.debug:
                        print(f"      Major recipe site with content ({len(response.text)} chars) - proceeding anyway")
                else:
                    return None
            else:
                # For other sites, require more substantial content to proceed despite blocking indicator
                if len(response.text) > 5000:
                    if self.debug:
                        print(f"      Substantial content ({len(response.text)} chars) - proceeding anyway")
                else:
                    return None
        
        # Parse HTML content
        recipe_content = self.extract_recipe_from_html(response.text)
        
        if recipe_content and len(recipe_content.strip()) > 50:
            if self.debug:
                print(f"      Successfully extracted {len(recipe_content)} characters")
                print(f"      Content preview: {recipe_content[:200]}...")
            return recipe_content
        else:
            if self.debug:
                print(f"      No substantial recipe content found")
                # Show a sample of what we got for debugging
                if response.text:
                    print(f"      HTML preview: {response.text[:300]}...")
            return None
    
    def _fetch_with_chrome_headers(self, url: str) -> Optional[str]:
        """Fetch with Chrome-specific headers for difficult sites"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = True  # Try SSL verification first
            
            if self.debug:
                print(f"      Making Chrome-style request to: {url} (SSL verification: ON)")
            
            response = session.get(url, timeout=20, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            
            response.raise_for_status()
            return self._process_response(response)
            
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Chrome headers - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled...")
            # Try without SSL verification as fallback
            try:
                if session:
                    session.verify = False
                    response = session.get(url, timeout=20, allow_redirects=True)
                    if self.debug:
                        print(f"      Got response (no SSL): {response.status_code}")
                    response.raise_for_status()
                    return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Chrome headers - SSL fallback also failed: {e2}")
                raise e
        except Exception as e:
            if self.debug:
                print(f"      Chrome headers - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_safari_headers(self, url: str) -> Optional[str]:
        """Fetch with Safari-specific headers as final fallback"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = True  # Try SSL verification first (even for difficult sites as final fallback)
            
            if self.debug:
                print(f"      Making Safari-style request to: {url} (SSL verification: ON)")
            
            # Add a longer delay for Safari simulation
            time.sleep(2)
            
            response = session.get(url, timeout=25, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            
            response.raise_for_status()
            return self._process_response(response)
            
        except requests.exceptions.SSLError as e:
            if self.debug:
                print(f"      Safari headers - SSL ERROR: {e}")
                print(f"      Retrying with SSL verification disabled (final fallback)...")
            # Try without SSL verification as final fallback
            try:
                if session:
                    session.verify = False
                    response = session.get(url, timeout=25, allow_redirects=True)
                    if self.debug:
                        print(f"      Got response (no SSL): {response.status_code}")
                    response.raise_for_status()
                    return self._process_response(response)
            except Exception as e2:
                if self.debug:
                    print(f"      Safari headers - SSL fallback also failed: {e2}")
                raise e
        except Exception as e:
            if self.debug:
                print(f"      Safari headers - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()

    def _fetch_with_extended_timeout(self, url: str) -> Optional[str]:
        """Fetch with extended timeouts and delays for very slow/stubborn sites"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://www.google.com/',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = False  # Disable SSL for stubborn sites
            
            if self.debug:
                print(f"      Making extended timeout request to: {url}")
            
            # Add extended delay for very slow sites
            time.sleep(5)
            
            response = session.get(url, timeout=60, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
                print(f"      Content length: {len(response.text)}")
            
            # Be very lenient with status codes
            if response.status_code in [200, 301, 302, 304, 403, 429] and len(response.text) > 100:
                return self._process_response_lenient(response)
            else:
                response.raise_for_status()
                return self._process_response_lenient(response)
                
        except Exception as e:
            if self.debug:
                print(f"      Extended timeout - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_no_ssl_verification(self, url: str) -> Optional[str]:
        """Last resort fetch with no SSL verification and very permissive settings"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'Referer': 'https://www.google.com/',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = False  # No SSL verification at all
            
            if self.debug:
                print(f"      Making no-SSL request to: {url}")
            
            # Extended delay and timeout for last resort
            time.sleep(7)
            
            response = session.get(url, timeout=90, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
                print(f"      Content length: {len(response.text)}")
                print(f"      Headers: {dict(list(response.headers.items())[:5])}")
            
            # Accept any response that has substantial content
            if response.status_code in [200, 301, 302, 304, 403, 429, 500, 503] and len(response.text) > 100:
                return self._process_response_lenient(response)
            else:
                if self.debug:
                    print(f"      Unexpected status or empty response")
                response.raise_for_status()
                return self._process_response_lenient(response)
                
        except Exception as e:
            if self.debug:
                print(f"      No SSL verification - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()

    def _fetch_with_firefox_headers(self, url: str) -> Optional[str]:
        """Fetch with Firefox-specific headers"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = False
            
            if self.debug:
                print(f"      Making Firefox-style request to: {url}")
            
            time.sleep(2)
            response = session.get(url, timeout=25, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            
            response.raise_for_status()
            return self._process_response(response)
            
        except Exception as e:
            if self.debug:
                print(f"      Firefox headers - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def _fetch_with_edge_headers(self, url: str) -> Optional[str]:
        """Fetch with Edge-specific headers as another fallback"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
        }
        
        session = None
        try:
            session = requests.Session()
            session.headers.update(headers)
            session.verify = False
            
            if self.debug:
                print(f"      Making Edge-style request to: {url}")
            
            time.sleep(2)
            response = session.get(url, timeout=25, allow_redirects=True)
            
            if self.debug:
                print(f"      Got response: {response.status_code}")
            
            response.raise_for_status()
            return self._process_response(response)
            
        except Exception as e:
            if self.debug:
                print(f"      Edge headers - ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            if session:
                session.close()

    def extract_recipe_from_html(self, html_content: str) -> Optional[str]:
        """Extract recipe content from HTML"""
        try:
            # Look for JSON-LD structured data first
            json_ld_patterns = [
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                r'<script[^>]*type=["\']application/ld\+json["\']>(.*?)</script>',
            ]
            
            for pattern in json_ld_patterns:
                matches = re.findall(pattern, html_content, re.DOTALL | re.IGNORECASE)
                for match in matches:
                    if self.debug:
                        print(f"    Found JSON-LD block: {match[:200]}...")
                    try:
                        # Clean up the JSON (remove comments, extra whitespace)
                        clean_json = re.sub(r'//.*?\n', '', match)  # Remove JS comments
                        clean_json = re.sub(r'/\*.*?\*/', '', clean_json, flags=re.DOTALL)  # Remove block comments
                        
                        json_data = json.loads(clean_json)
                        recipe_data = self.extract_from_json_ld(json_data)
                        if recipe_data:
                            if self.debug:
                                print(f"    Successfully extracted recipe from JSON-LD")
                            return recipe_data
                    except json.JSONDecodeError as e:
                        if self.debug:
                            print(f"    JSON parsing error: {e}")
                        continue
            
            if self.debug:
                print(f"    No valid JSON-LD found, trying HTML parsing...")
            
            # Look for microdata or specific recipe containers
            recipe_selectors = [
                r'<div[^>]*class=["\'][^"\']*recipe[^"\']*["\'][^>]*>(.*?)</div>',
                r'<article[^>]*class=["\'][^"\']*recipe[^"\']*["\'][^>]*>(.*?)</article>',
                r'<section[^>]*class=["\'][^"\']*recipe[^"\']*["\'][^>]*>(.*?)</section>',
                r'<div[^>]*itemtype=["\'][^"\']*Recipe[^"\']*["\'][^>]*>(.*?)</div>',
            ]
            
            for selector in recipe_selectors:
                matches = re.findall(selector, html_content, re.DOTALL | re.IGNORECASE)
                if matches:
                    # Take the longest match (most likely to be the main recipe)
                    recipe_html = max(matches, key=len)
                    return self.html_to_text(recipe_html)
            
            # Fallback: look for common recipe text patterns
            return self.extract_recipe_text_patterns(html_content)
            
        except Exception as e:
            if self.debug:
                print(f"    Error parsing HTML: {e}")
            return None
    
    def extract_from_json_ld(self, json_data: Any) -> Optional[str]:
        """Extract recipe data from JSON-LD structured data"""
        try:
            if self.debug:
                print(f"    Processing JSON-LD data type: {type(json_data)}")
            
            # Handle both single objects and arrays
            recipes_to_check = []
            if isinstance(json_data, list):
                if self.debug:
                    print(f"    JSON-LD is array with {len(json_data)} items")
                for item in json_data:
                    if isinstance(item, dict):
                        recipes_to_check.append(item)
            elif isinstance(json_data, dict):
                recipes_to_check.append(json_data)
            
            # Look for Recipe objects
            recipe_obj = None
            for item in recipes_to_check:
                if self.debug:
                    item_type = item.get('@type', 'unknown')
                    print(f"    Checking item with @type: {item_type}")
                
                # Check direct @type
                if item.get('@type') == 'Recipe':
                    recipe_obj = item
                    break
                
                # Check if @type is an array containing 'Recipe'
                item_type = item.get('@type', [])
                if isinstance(item_type, list) and 'Recipe' in item_type:
                    recipe_obj = item
                    break
                
                # Check nested @graph property (common in some implementations)
                if '@graph' in item:
                    graph_items = item['@graph']
                    if isinstance(graph_items, list):
                        for graph_item in graph_items:
                            if isinstance(graph_item, dict):
                                graph_type = graph_item.get('@type')
                                if graph_type == 'Recipe' or (isinstance(graph_type, list) and 'Recipe' in graph_type):
                                    recipe_obj = graph_item
                                    break
                    if recipe_obj:
                        break
            
            if not recipe_obj:
                if self.debug:
                    print(f"    No Recipe object found in JSON-LD")
                return None
            
            if self.debug:
                print(f"    Found Recipe object, extracting data...")
                if 'name' in recipe_obj:
                    print(f"    Recipe name: {recipe_obj['name']}")
            
            # Build recipe text from structured data
            recipe_parts = []
            
            # Add name
            if 'name' in recipe_obj:
                recipe_parts.append(f"# {recipe_obj['name']}")
            
            # Add description
            if 'description' in recipe_obj:
                desc = recipe_obj['description']
                if isinstance(desc, str):
                    recipe_parts.append(f"\n{desc}")
            
            # Add yield/servings
            if 'recipeYield' in recipe_obj:
                yield_val = recipe_obj['recipeYield']
                if isinstance(yield_val, list) and yield_val:
                    yield_val = yield_val[0]
                recipe_parts.append(f"\nYield: {yield_val}")
            
            # Add timing information
            times = []
            if 'prepTime' in recipe_obj:
                times.append(f"Prep: {self.parse_duration(recipe_obj['prepTime'])}")
            if 'cookTime' in recipe_obj:
                times.append(f"Cook: {self.parse_duration(recipe_obj['cookTime'])}")
            if 'totalTime' in recipe_obj:
                times.append(f"Total: {self.parse_duration(recipe_obj['totalTime'])}")
            if times:
                recipe_parts.append(f"\n{' | '.join(times)}")
            
            # Add ingredients
            if 'recipeIngredient' in recipe_obj:
                recipe_parts.append("\n## Ingredients")
                ingredients = recipe_obj['recipeIngredient']
                if isinstance(ingredients, list):
                    for ingredient in ingredients:
                        if isinstance(ingredient, str):
                            recipe_parts.append(f"• {ingredient.strip()}")
                        elif isinstance(ingredient, dict) and 'text' in ingredient:
                            recipe_parts.append(f"• {ingredient['text'].strip()}")
            
            # Add instructions
            if 'recipeInstructions' in recipe_obj:
                recipe_parts.append("\n## Instructions")
                instructions = recipe_obj['recipeInstructions']
                if isinstance(instructions, list):
                    for i, instruction in enumerate(instructions, 1):
                        text = ""
                        if isinstance(instruction, dict):
                            text = instruction.get('text', '')
                            if not text:
                                text = instruction.get('name', '')
                        elif isinstance(instruction, str):
                            text = instruction
                        
                        if text:
                            # Clean up the text
                            text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
                            text = html.unescape(text)  # Decode HTML entities
                            recipe_parts.append(f"{i}. {text.strip()}")
            
            # Add nutrition info if available
            if 'nutrition' in recipe_obj:
                nutrition = recipe_obj['nutrition']
                if isinstance(nutrition, dict):
                    nutrition_items = []
                    for key, value in nutrition.items():
                        if key != '@type' and value and str(value).strip():
                            clean_key = key.replace('Content', '').replace('content', '')
                            nutrition_items.append(f"{clean_key}: {value}")
                    
                    if nutrition_items:
                        recipe_parts.append("\n## Nutrition")
                        recipe_parts.extend(nutrition_items)
            
            result = '\n'.join(recipe_parts) if recipe_parts else None
            
            if self.debug and result:
                print(f"    Generated recipe text ({len(result)} chars)")
                print(f"    Preview: {result[:200]}...")
            
            return result
            
        except Exception as e:
            if self.debug:
                print(f"    Error parsing JSON-LD: {e}")
            return None
    
    def parse_duration(self, duration_str: str) -> str:
        """Parse ISO 8601 duration to human readable format"""
        if not duration_str:
            return ""
        
        # Handle ISO 8601 format like PT15M, PT1H30M
        if duration_str.startswith('PT'):
            duration_str = duration_str[2:]  # Remove PT
            hours = re.search(r'(\d+)H', duration_str)
            minutes = re.search(r'(\d+)M', duration_str)
            
            parts = []
            if hours:
                parts.append(f"{hours.group(1)}h")
            if minutes:
                parts.append(f"{minutes.group(1)}m")
            
            return ' '.join(parts) if parts else duration_str
        
        return duration_str
    
    def html_to_text(self, html_content: str) -> str:
        """Convert HTML content to plain text"""
        # Remove script and style elements
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Convert common HTML elements to text
        html_content = re.sub(r'<br[^>]*>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<p[^>]*>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</p>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<div[^>]*>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</div>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<li[^>]*>', '\n• ', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</li>', '', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<h[1-6][^>]*>', '\n## ', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</h[1-6]>', '\n', html_content, flags=re.IGNORECASE)
        
        # Remove all remaining HTML tags
        html_content = re.sub(r'<[^>]+>', '', html_content)
        
        # Decode HTML entities
        html_content = html.unescape(html_content)
        
        # Clean up whitespace
        html_content = re.sub(r'\n\s*\n', '\n\n', html_content)
        html_content = re.sub(r'[ \t]+', ' ', html_content)
        
        return html_content.strip()
    
    def extract_recipe_text_patterns(self, html_content: str) -> Optional[str]:
        """Extract recipe using common text patterns"""
        # Convert to text first
        text_content = self.html_to_text(html_content)
        
        # Look for recipe keywords
        recipe_keywords = [
            r'ingredients?:?\s*\n',
            r'directions?:?\s*\n',
            r'instructions?:?\s*\n',
            r'method:?\s*\n',
            r'preparation:?\s*\n'
        ]
        
        # Find recipe sections
        for keyword in recipe_keywords:
            if re.search(keyword, text_content, re.IGNORECASE):
                # Found recipe content
                return text_content
        
        # If no clear recipe structure, check for measurement patterns
        measurement_patterns = [
            r'\d+\s*(cups?|tablespoons?|teaspoons?|pounds?|ounces?)',
            r'\d+/\d+\s*(cups?|tablespoons?|teaspoons?)',
            r'[¼½¾⅓⅔⅛⅜⅝⅞]\s*(cups?|tablespoons?|teaspoons?)'
        ]
        
        measurement_count = 0
        for pattern in measurement_patterns:
            measurement_count += len(re.findall(pattern, text_content, re.IGNORECASE))
        
        # If we found several measurements, it's likely a recipe
        if measurement_count >= 3:
            return text_content
        
        return None

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

    def extract_source_url(self, content: str, recipe_title: str = "") -> str:
        """Extract potential recipe source URL from content"""
        if not content:
            return ""
        
        if self.debug:
            print(f"    Processing content length: {len(content)}")
            print(f"    Content preview: {content[:200]}...")
        
        # PRIORITY 1: Look for explicit source URL tags first
        source_url_patterns = [
            r'<source-url>\s*(https?://[^<>"\']+)\s*</source-url>',  # <source-url>...</source-url>
            r'--en-clipped-source-url:\s*(https?://[^\s<>"\']+)'     # Evernote clipped URLs
        ]
        
        for pattern in source_url_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                if self.debug:
                    print(f"    Found explicit source URL(s): {matches}")
                # Clean the explicit source URL too
                explicit_url = matches[0]
                original_url = explicit_url
                
                # Apply the same cleaning logic to explicit URLs
                while explicit_url.endswith(';') or explicit_url.endswith('/'):
                    if explicit_url.endswith(';'):
                        explicit_url = explicit_url[:-1]  # Remove trailing semicolon
                    if explicit_url.endswith('/'):
                        explicit_url = explicit_url[:-1]  # Remove trailing slash
                
                if self.debug and original_url != explicit_url:
                    print(f"    Cleaned explicit source URL: '{original_url}' -> '{explicit_url}'")
                
                # Return the first (usually only) explicit source URL
                return explicit_url
        
        # PRIORITY 2: Look for other URL patterns - stop only at sentence-ending punctuation
        url_patterns = [
            # Standard URL pattern - stop at whitespace, quotes, or sentence-ending punctuation
            r'https?://[^\s<>"\']+',
            # More specific pattern for common recipe sites
            r'https?://(?:www\.)?[\w\-]+\.com[^\s<>"\']*',
            # SeriousEats specific
            r'https?://(?:www\.)?seriouseats\.com[^\s<>"\']*',
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
        
        # Filter out invalid URLs
        valid_urls = []
        invalid_patterns = [
            r'\.dtd',  # XML DTD files
            r'\.xsd',  # XML Schema files
            r'\.xml',  # Generic XML files
            r'evernote\.com',  # Evernote URLs
            r'xml\..*\.com',  # XML-related domains
            r'xmlns\.',  # XML namespace URLs
            r'w3\.org',  # W3C specification URLs
            r'example\.com',  # Example/placeholder URLs
            r'localhost',  # Local URLs
            r'127\.0\.0\.1',  # Local IP
            r'\.css',  # CSS files
            r'\.js',  # JavaScript files
            r'\.png',  # Image files
            r'\.jpg',  # Image files
            r'\.gif',  # Image files
            r'facebook\.com',  # Social media
            r'twitter\.com',  # Social media
            r'instagram\.com',  # Social media
            r'pinterest\.com',  # Social media
            r'linkedin\.com',  # Social media
            r'youtube\.com',  # Video sharing
            r'youtu\.be',  # Video sharing short links
            r'api\.whatsapp\.com',  # WhatsApp sharing URLs
            r'wa\.me',  # WhatsApp short links
            r't\.co',  # Twitter short links
            r'bit\.ly',  # Bitly short links
            r'tinyurl\.com',  # TinyURL short links
            r'mailto:',  # Email links
        ]
        
        for url in urls:
            url_lower = url.lower()
            # Skip URLs that match invalid patterns
            if any(re.search(pattern, url_lower) for pattern in invalid_patterns):
                if self.debug:
                    print(f"    Skipping invalid URL: {url}")
                continue
            
            # Skip URLs with sharing/tracking parameters that make them look like sharing URLs
            sharing_params = ['text=', 'url=', 'smid=', 'utm_source=', 'utm_medium=']
            if any(param in url_lower for param in sharing_params):
                if self.debug:
                    print(f"    Skipping sharing URL: {url}")
                continue
            
            # Must be HTTP/HTTPS
            if not url_lower.startswith(('http://', 'https://')):
                continue
                
            # Must have a reasonable length
            if len(url) < 10 or len(url) > 500:
                continue
            
            # Clean up URLs with unwanted suffixes
            clean_url = self.clean_recipe_url(url)
            if clean_url != url and self.debug:
                print(f"    Cleaned URL: {url} -> {clean_url}")
            
            valid_urls.append(clean_url)
        
        if not valid_urls:
            if self.debug:
                print("    No valid URLs found")
            return ""
        
        if self.debug:
            print(f"    Found valid URLs: {valid_urls}")
        
        # Score URLs based on how likely they are to be recipe sources
        recipe_keywords = [
            'recipe', 'food', 'cooking', 'kitchen', 'chef', 'cuisine', 'dish',
            'allrecipes', 'foodnetwork', 'epicurious', 'bonappetit', 'seriouseats',
            'tasteofhome', 'delish', 'food52', 'yummly', 'budget', 'meal',
            'ingredient', 'bake', 'cook', 'serious', 'eats', 'blog', 'soup',
            'midwest', 'foodie', 'vegan', 'lentil', 'tortilla'
        ]
        
        # Extract words from recipe title for URL matching
        title_words = []
        if recipe_title:
            # Clean and split title into words
            clean_title = re.sub(r'[^\w\s-]', '', recipe_title.lower())
            title_words = [word.strip() for word in clean_title.split() if len(word) > 2]
            if self.debug:
                print(f"    Recipe title words for URL matching: {title_words}")
        
        scored_urls = []
        for url in valid_urls:
            score = 1  # Start with base score of 1 for any valid URL
            url_lower = url.lower()
            
            # HIGHEST PRIORITY: Boost score significantly if title words appear in URL
            title_word_matches = 0
            for word in title_words:
                if word in url_lower:
                    title_word_matches += 1
                    score += 5  # Strong boost for each title word match
            
            if title_word_matches > 0 and self.debug:
                print(f"    Title word matches in URL: {title_word_matches} words")
            
            # PENALTY: Heavily penalize URLs with unwanted segments (even after cleaning)
            unwanted_url_parts = [
                '/print', '/comment', '/respond', '/feed', '/rss', '/trackback',
                '/amp', '/mobile', 'facebook.com', 'twitter.com', 'instagram.com',
                'pinterest.com', 'linkedin.com', 'youtube.com', 'youtu.be'
            ]
            
            penalty_count = 0
            for unwanted in unwanted_url_parts:
                if unwanted in url_lower:
                    penalty_count += 1
                    score -= 10  # Heavy penalty for unwanted segments
            
            if penalty_count > 0 and self.debug:
                print(f"    URL penalty for unwanted segments: -{penalty_count * 10}")
            
            # BONUS: Prefer shorter, cleaner URLs (main recipe pages)
            # Count path segments - fewer is usually better for recipe pages
            path_segments = url.replace('https://', '').replace('http://', '').count('/')
            if path_segments <= 2:  # domain.com/recipe-name
                score += 3
            elif path_segments == 3:  # domain.com/category/recipe-name
                score += 2
            elif path_segments >= 5:  # very deep URLs are usually not main recipe pages
                score -= 2
            
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
            
            # Boost for food blogs (common pattern)
            if any(pattern in url_lower for pattern in ['foodie', 'blog', 'kitchen', 'recipe']):
                score += 3
            
            # Boost for recipe-like paths containing dish names or ingredients
            recipe_path_indicators = [
                'soup', 'salad', 'chicken', 'beef', 'pasta', 'bread', 'cake', 
                'cookie', 'vegan', 'vegetarian', 'healthy', 'easy', 'quick'
            ]
            for indicator in recipe_path_indicators:
                if indicator in url_lower:
                    score += 1
            
            # Penalize very long URLs or those with tracking parameters
            if len(url) > 150 or any(param in url_lower for param in ['utm_', 'ref=', 'src=']):
                score -= 1
            
            scored_urls.append((score, url))
            if self.debug:
                print(f"    URL: {url[:70]}... Score: {score} (title matches: {title_word_matches}, penalties: {penalty_count})")
        
        # Sort by score descending, then by URL length ascending (prefer shorter URLs when scores are equal)
        scored_urls.sort(key=lambda x: (-x[0], len(x[1])))
        
        # Return the highest scoring URL, or first URL if no good matches
        if scored_urls:
            best_url = scored_urls[0][1]
            
            # Clean up the URL - only remove trailing semicolons and /;
            original_best_url = best_url
            
            # Loop through and remove trailing ; and / until none remain
            while best_url.endswith(';') or best_url.endswith('/'):
                if best_url.endswith(';'):
                    best_url = best_url[:-1]  # Remove trailing semicolon
                if best_url.endswith('/'):
                    best_url = best_url[:-1]  # Remove trailing slash
            
            if self.debug and original_best_url != best_url:
                print(f"    URL punctuation cleaned: '{original_best_url}' -> '{best_url}'")
            
            if self.debug:
                print(f"    Selected URL: {best_url}")
                if len(scored_urls) > 1:
                    print(f"    Other candidates:")
                    for score, candidate_url in scored_urls[1:3]:  # Show top 3 alternatives
                        print(f"      Score {score}: {candidate_url}")
            return best_url
        
        return ""

    def extract_ingredients(self, content: str, recipe_title: str = "Unknown Recipe") -> List[str]:
        """Extract ingredients from content"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        ingredients = []
        substitution_notes = []
        
        if self.debug:
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
                if self.debug:
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
                    if self.debug:
                        print(f"ACCEPTED (in ingredient section): '{clean_line}'")
                    enhanced_ingredient = self.enhance_ingredient_with_substitutions(clean_line, substitution_notes)
                    ingredients.append(enhanced_ingredient)
                    if self.debug:
                        print(f"ADDED INGREDIENT (pass 1 - section context): '{enhanced_ingredient}'")
                    continue
            
            if is_ingredient and len(line) < 200:  # Must pass ingredient test AND length check
                # Try to match with substitution notes
                enhanced_ingredient = self.enhance_ingredient_with_substitutions(clean_line, substitution_notes)
                ingredients.append(enhanced_ingredient)
                if self.debug:
                    print(f"ADDED INGREDIENT (pass 1): '{enhanced_ingredient}'")
            else:
                # Debug why this line was rejected
                if self.debug:
                    if len(line) >= 200:
                        print(f"REJECTED (too long): '{line[:50]}...' ({len(line)} chars)")
                    else:
                        print(f"REJECTED LINE: '{line[:60]}...' (is_ingredient={is_ingredient}, in_section={in_ingredients_section})")
        
        # If no ingredients found, try pattern matching on all lines
        if not ingredients:
            if self.debug:
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
                        if self.debug:
                            print(f"ADDED INGREDIENT (pass 2): '{enhanced_ingredient}'")
        
        # If still no ingredients, use first few short lines (but apply strict filters)
        if not ingredients:
            if self.debug:
                print("No ingredients found in pass 2, trying pass 3 (first 10 lines)...")
            for i, line in enumerate(lines[:10]):
                if self.debug:
                    print(f"  Line {i+1}: '{line[:80]}...'")
                if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                    if self.debug:
                        print(f"    REJECTED: Contains URL")
                    continue
                if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                    if self.debug:
                        print(f"    REJECTED: Contains page reference")
                    continue
                if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
                    if self.debug:
                        print(f"    REJECTED: Contains serving info")
                    continue
                if re.search(r'\b(prep|cook|total)\s+time\b|\b\d+\s+(min|minutes|hrs?|hours?)\b', line.lower()):
                    if self.debug:
                        print(f"    REJECTED: Contains time info")
                    continue
                    
                # MUST pass the strict ingredient test - no fallback to instruction test
                if (5 < len(line) < 150 and 
                    self.is_ingredient_line(line)):  # Use strict ingredient test, not instruction test
                    enhanced_ingredient = self.enhance_ingredient_with_substitutions(line, substitution_notes)
                    ingredients.append(enhanced_ingredient)
                    if self.debug:
                        print(f"    ADDED INGREDIENT (pass 3): '{enhanced_ingredient}'")
                else:
                    if self.debug:
                        is_ingredient = self.is_ingredient_line(line)
                        print(f"    REJECTED: length={len(line)}, is_ingredient={is_ingredient}")
        
        if self.debug:
            print(f"=== FINAL INGREDIENT COUNT: {len(ingredients)} ===")
            for i, ing in enumerate(ingredients):
                print(f"  {i+1}. {ing}")
            print("=" * 50)
            print(f"# END INGREDIENT EXTRACTION - {recipe_title}")
            print(f"{'#'*80}")
        
        # If still no ingredients, use first few short lines (but apply strict filters)
        if not ingredients:
            if self.debug:
                print("No ingredients found in pass 2, trying pass 3 (first 10 lines)...")
            for i, line in enumerate(lines[:10]):
                if self.debug:
                    print(f"  Line {i+1}: '{line[:80]}...'")
                if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                    if self.debug:
                        print(f"    REJECTED: Contains URL")
                    continue
                if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                    if self.debug:
                        print(f"    REJECTED: Contains page reference")
                    continue
                if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
                    if self.debug:
                        print(f"    REJECTED: Contains serving info")
                    continue
                if re.search(r'\b(prep|cook|total)\s+time\b|\b\d+\s+(min|minutes|hrs?|hours?)\b', line.lower()):
                    if self.debug:
                        print(f"    REJECTED: Contains time info")
                    continue
                    
                # MUST pass the strict ingredient test - no fallback to instruction test
                if (5 < len(line) < 150 and 
                    self.is_ingredient_line(line)):  # Use strict ingredient test, not instruction test
                    enhanced_ingredient = self.enhance_ingredient_with_substitutions(line, substitution_notes)
                    ingredients.append(enhanced_ingredient)
                    if self.debug:
                        print(f"    ADDED INGREDIENT (pass 3): '{enhanced_ingredient}'")
                else:
                    if self.debug:
                        is_ingredient = self.is_ingredient_line(line)
                        print(f"    REJECTED: length={len(line)}, is_ingredient={is_ingredient}")
        
        if self.debug:
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
        if not line or len(line.strip()) < 3:
            return False
        
        # Clean the line first to remove bullet points and other formatting
        clean_line = self.clean_ingredient_line(line)
        if not clean_line or len(clean_line.strip()) < 3:
            return False
        
        line_lower = clean_line.lower()
        
        # EARLY CHECK: Special case for "to taste" and "salt and pepper" patterns - these are usually ingredients
        # But ONLY if they don't start with instruction verbs AND are short standalone lines
        first_word = line_lower.split()[0] if line_lower.split() else ""
        instruction_starters_early = ['uncover', 'stir', 'mix', 'add', 'heat', 'cook', 'remove', 'serve', 'drain', 'transfer', 'top']
        
        if (re.search(r'\bto\s+taste\b', line_lower) or re.search(r'\bsalt\s+and\s+pepper\b', line_lower)):
            # Only accept as ingredient if it's a short line that doesn't start with instruction verbs
            if first_word not in instruction_starters_early and len(clean_line) < 50:
                return True
        
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
        if first_word in instruction_starters:
            return False
        
        # STEP 2: Reject numbered instructions (1., 2., Step 1, etc.) - but NOT ingredient quantities
        # Only reject if number is followed by period/parenthesis/dash AND space (like "1. Mix" or "1) Heat" or "1 - Stir")
        # NOT if it's followed by a measurement unit (like "1 cup" or "12 ounces")
        if re.match(r'^\d+[\.\)\-]\s', clean_line) or line_lower.startswith('step'):
            return False
        
        # STEP 3: Reject section headers
        section_headers = [
            'ingredients', 'directions', 'instructions', 'method', 'preparation',
            'for the', 'herb blend', 'everything else', 'sauce', 'topping',
            'marinade', 'dressing', 'garnish', 'notes', 'variations'
        ]
        
        if any(line_lower.startswith(header) for header in section_headers):
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
            return False
        
        # STEP 5: Reject very long lines (likely instructions)
        if len(line) > 200:
            return False
        
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
        for pattern in ingredient_patterns:
            if re.match(pattern, clean_line, re.IGNORECASE):
                return True
        
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
            return False
        
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
        
        if self.debug:
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
            # Skip URLs completely
            if any(url_part in line.lower() for url_part in ['http', 'www.', '.com', '.org']):
                if self.debug:
                    print(f"    DEBUG: SKIPPED - contains URL")
                continue
                
            # Skip page numbers and references
            if re.search(r'\bpage\s+\d+\b|\bp\.\s*\d+\b', line.lower()):
                if self.debug:
                    print(f"    DEBUG: SKIPPED - contains page reference")
                continue
                
            # Skip ONLY standalone time information like "Prep time: 15 minutes" but NOT cooking instructions with time
            if re.search(r'\b(prep|cook|total)\s+time\b', line.lower()):
                if self.debug:
                    print(f"    DEBUG: SKIPPED - contains standalone time info")
                continue
                
            # ALLOW yield/serving info in instructions (keep it as useful recipe metadata)
            # Skip serving/yield info
            # if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line.lower()):
            #     continue
            
            # Skip substitution notes that are already in ingredients
            if any(note in line.lower() for note in substitution_notes):
                if self.debug:
                    print(f"    DEBUG: SKIPPED - substitution note")
                continue
            
            # Check if this is an image placeholder
            if re.match(r'\[IMAGE_\d+\]', line):
                instructions.append(line)  # Keep image placeholders as separate instructions
                if self.debug:
                    print(f"    DEBUG: ADDED as IMAGE placeholder")
            elif (self.is_instruction_line(line) and 
                  len(line) > 15 and 
                  not self.is_ingredient_line(line)):  # Make sure it's not also an ingredient
                clean_line = self.clean_instruction_line(line)
                if clean_line:
                    instructions.append(clean_line)
                    if self.debug:
                        print(f"    DEBUG: ADDED as INSTRUCTION: '{clean_line[:50]}...'")
                else:
                    if self.debug:
                        print(f"    DEBUG: REJECTED - clean_instruction_line returned empty/None")
            else:
                if self.debug:
                    is_instruction = self.is_instruction_line(line)
                    is_ingredient = self.is_ingredient_line(line)
                    print(f"    DEBUG: REJECTED instruction - is_instruction={is_instruction}, is_ingredient={is_ingredient}, len={len(line)}")
                    if is_instruction and is_ingredient:
                        print(f"    DEBUG: Line classified as BOTH instruction and ingredient - rejecting as instruction")
        
        if self.debug:
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
        # Remove bullet points and checkmarks
        line = re.sub(r'^[•\-\*☐✓]\s*', '', line)
        # Remove leading numbers with periods/parentheses
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        
        return line.strip()

    def is_instruction_line(self, line: str) -> bool:
        """Check if line looks like an instruction"""
        line_lower = line.lower()
        
        # Exclude serving/yield info first
        if re.search(r'\b(serves?|servings?|yield|makes?)\s+\d+\b', line_lower):
            return False
        
        # Exclude time information (but not cooking instructions that mention time)
        # Only reject standalone time references like "Prep time: 15 minutes" or "Cook time: 30 min"
        if re.search(r'\b(prep|cook|total)\s+time\b', line_lower):
            return False
        
        # Must be longer than typical ingredients
        if len(line) < 20:
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
        if self.debug:
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
                    if re.search(pattern, instruction_lower):
                        is_likely_ingredient = True
                        matched_pattern = pattern
                        break
            
            if is_likely_ingredient:
                if self.debug:
                    print(f"  MOVING TO INGREDIENTS: '{instruction[:80]}...'")
                    print(f"    Matched pattern: {matched_pattern}")
                
                # Clean up the instruction to make it more ingredient-like
                clean_ingredient = instruction
                
                # Tidy up dashes and spacing
                clean_ingredient = re.sub(r'\s+', ' ', clean_ingredient).strip()
                
                new_ingredients.append(clean_ingredient)
                moved_count += 1
                if self.debug:
                    print(f"    CLEANED TO: '{clean_ingredient}'")
            else:
                new_instructions.append(instruction)
        
        if self.debug:
            print(f"  MOVED {moved_count} items from instructions to ingredients")
            print(f"~ END POST-PROCESSING - {recipe_title}")
            print(f"{'~'*80}")
        
        return new_ingredients, new_instructions


def test_url_fetch(url: str, debug: bool = True):
    """Test function to verify URL fetching works"""
    print(f"Testing URL: {url}")
    
    # Create a minimal converter instance for testing
    import tempfile
    temp_dir = tempfile.mkdtemp()
    converter = EvernoteToNextcloudConverter(temp_dir, "test.zip", debug=debug)
    
    try:
        result = converter.fetch_recipe_from_url(url)
        if result:
            print(f"SUCCESS: Fetched {len(result)} characters")
            print(f"Preview: {result[:200]}...")
            return True
        else:
            print("FAILED: No content returned")
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description='Convert Evernote .enex files to Nextcloud Recipes export format. '
                   'Will attempt to fetch fresh content from source URLs when available.'
    )
    parser.add_argument('input_dir', help='Directory containing .enex files')
    parser.add_argument('output_file', help='Output zip file path')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--no-web-fetch', action='store_true', 
                       help='Disable fetching content from source URLs, use only .enex content')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_dir):
        print(f"Error: Directory '{args.input_dir}' not found")
        return 1
    
    exporter = EvernoteToNextcloudConverter(args.input_dir, args.output_file, debug=args.debug)
    
    # Set web fetch option - enabled by default, disable only if requested
    if args.no_web_fetch:
        exporter.enable_web_fetch = False
    
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
