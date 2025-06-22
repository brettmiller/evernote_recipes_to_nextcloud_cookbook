The script parses a directory of Evernote .enex export files that contain recipes and properly converts them to a Nextcloud Cookbook zip archive. That archive can be imported into several other Cookbook webapps, including Mealie which is what it was tested with and originally written for.

## Features

- **Intelligent Recipe Processing**: Automatically extracts ingredients, instructions, and descriptions from Evernote notes
- **Web Content Enhancement**: When source URLs are found in notes, fetches fresh content from original recipe websites for better accuracy
- **JSON-LD Priority**: Prioritizes structured data (JSON-LD) from recipe websites for maximum accuracy
- **Image Support**: Downloads and includes recipe images from web sources, or extracts embedded images from Evernote notes
- **Flexible Tag/Category Management**: Add custom tags and categories, or override defaults completely
- **Multiple Input Formats**: Process single .enex files or entire directories
- **Schema.org Compatibility**: Output format works with Nextcloud Recipes, Mealie, and other Recipe schema-compatible systems

## Requirements

```bash
pip install requests
```

## Usage

```text
evernote_to_nextcloud_cookbook.py [-h|--help] [--tags TAG1,TAG2,...] [--tags-override TAG1,TAG2,...] [--categories CAT1,CAT2,...] [--categories-override CAT1,CAT2,...] [--debug] [--no-web-fetch] [--test-url URL] input [output]
```

### Positional Arguments

- `input` - Input .enex file or directory containing .enex files
- `output` - Output zip file (default: recipes_export.zip)

### Tag Options

- `-t, --tags TAG1,TAG2,...` - Add additional tags to all recipes (comma-separated)
- `-T, --tags-override TAG1,TAG2,...` - Replace default tags completely (comma-separated)

### Category Options

- `-c, --categories CAT1,CAT2,...` - Add additional categories to all recipes (comma-separated)
- `-C, --categories-override CAT1,CAT2,...` - Replace default/existing categories completely (comma-separated)

### Processing Options

- `--debug` - Enable detailed debug output for troubleshooting
- `--no-web-fetch` - Disable web content fetching (use only Evernote content)

### Testing Options

- `--test-url URL` - Test URL fetching with the given URL (for debugging)

## Examples

```bash
# Convert single file
./evernote_to_nextcloud_cookbook.py recipes.enex

# Convert all .enex files in directory
./evernote_to_nextcloud_cookbook.py ~/Downloads/evernote_exports/

# Add custom tags
./evernote_to_nextcloud_cookbook.py recipes.enex -t "vegetarian,quick"

# Override default tags completely
./evernote_to_nextcloud_cookbook.py recipes.enex -T "vegan,healthy"

# Add custom categories
./evernote_to_nextcloud_cookbook.py recipes.enex -c "Dessert,Quick"

# Override default category completely
./evernote_to_nextcloud_cookbook.py recipes.enex -C "Main Dish,Italian"

# Enable debug output
./evernote_to_nextcloud_cookbook.py recipes.enex --debug

# Test URL fetching
./evernote_to_nextcloud_cookbook.py --test-url "https://example.com/recipe"
```

## How It Works

1. **Content Analysis**: The script analyzes each Evernote note to identify recipe content
2. **URL Extraction**: Looks for source URLs in the note content
3. **Web Enhancement**: If URLs are found, attempts to fetch fresh content with multiple fallback strategies
4. **JSON-LD Priority**: Prioritizes structured recipe data (JSON-LD) from websites for maximum accuracy
5. **Fallback Processing**: Falls back to HTML parsing or Evernote content if web fetching fails
6. **Recipe Assembly**: Combines the best available data into Schema.org Recipe format
7. **Image Handling**: Downloads web images or extracts Evernote embedded images
8. **Export Creation**: Packages everything into a Nextcloud-compatible zip archive

## Notes

- Output format is compatible with Nextcloud Recipes and other Schema.org Recipe systems
- Web fetching prioritizes JSON-LD structured data for best accuracy
- Images are downloaded and included when available from web sources
- For JSON-LD recipes from web sources, existing categories are preserved unless overridden
- The script respects websites with delays and multiple fallback strategies for difficult sites

</br>
For those who care it was written with the help of Claude Sonnet 4 (via Github Copilot).
