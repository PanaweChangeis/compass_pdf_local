"""
Textract to Cognee Loader V2 - Page-Based Correlation
------------------------------------------------------

Enhanced version that creates explicit page-text-image correlation for better
knowledge graph relationships in Cognee.

Key improvements:
1. Splits text by page into separate markdown files
2. Pairs each text file with its corresponding image
3. Adds metadata linking text to images
4. Progress logging for each page pair
"""

import json
import boto3
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class TextractPageExtractor:
    """Extracts text from Textract JSON on a per-page basis."""
    
    def __init__(self, textract_blocks: List[Dict[str, Any]]):
        """
        Initialize extractor with Textract blocks.
        
        Args:
            textract_blocks: List of Textract block dictionaries
        """
        self.blocks = textract_blocks
        self.blocks_by_id = {block['Id']: block for block in textract_blocks}
        self.pages = self._group_blocks_by_page()
    
    def _group_blocks_by_page(self) -> Dict[int, List[Dict[str, Any]]]:
        """Group blocks by page number."""
        pages = {}
        for block in self.blocks:
            if 'Page' in block:
                page_num = block['Page']
                if page_num not in pages:
                    pages[page_num] = []
                pages[page_num].append(block)
        return pages
    
    def _get_block_text(self, block: Dict[str, Any]) -> str:
        """Extract text from a block."""
        if block['BlockType'] == 'WORD':
            return block.get('Text', '')
        elif block['BlockType'] == 'LINE':
            # Get text from child WORD blocks
            if 'Relationships' in block:
                for rel in block['Relationships']:
                    if rel['Type'] == 'CHILD':
                        words = []
                        for child_id in rel['Ids']:
                            if child_id in self.blocks_by_id:
                                child = self.blocks_by_id[child_id]
                                if child['BlockType'] == 'WORD':
                                    words.append(child.get('Text', ''))
                        return ' '.join(words)
        return block.get('Text', '')
    
    def _extract_table_text(self, table_block: Dict[str, Any]) -> str:
        """Extract text from a TABLE block in markdown format."""
        if 'Relationships' not in table_block:
            return ""
        
        # Build table structure
        cells = {}
        max_row = 0
        max_col = 0
        
        for rel in table_block['Relationships']:
            if rel['Type'] == 'CHILD':
                for cell_id in rel['Ids']:
                    if cell_id in self.blocks_by_id:
                        cell = self.blocks_by_id[cell_id]
                        if cell['BlockType'] == 'CELL':
                            row = cell.get('RowIndex', 1)
                            col = cell.get('ColumnIndex', 1)
                            max_row = max(max_row, row)
                            max_col = max(max_col, col)
                            
                            # Get cell text
                            cell_text = ""
                            if 'Relationships' in cell:
                                for cell_rel in cell['Relationships']:
                                    if cell_rel['Type'] == 'CHILD':
                                        words = []
                                        for word_id in cell_rel['Ids']:
                                            if word_id in self.blocks_by_id:
                                                word = self.blocks_by_id[word_id]
                                                if word['BlockType'] == 'WORD':
                                                    words.append(word.get('Text', ''))
                                        cell_text = ' '.join(words)
                            
                            cells[(row, col)] = cell_text
        
        # Build markdown table
        if max_row == 0 or max_col == 0:
            return ""
        
        lines = []
        for row in range(1, max_row + 1):
            row_cells = []
            for col in range(1, max_col + 1):
                cell_text = cells.get((row, col), "")
                row_cells.append(cell_text)
            lines.append("| " + " | ".join(row_cells) + " |")
            
            # Add separator after header row
            if row == 1:
                lines.append("|" + "|".join(["---" for _ in range(max_col)]) + "|")
        
        return "\n".join(lines)
    
    def extract_page_text(self, page_num: int, document_id: str) -> str:
        """
        Extract text for a specific page with metadata linking to image.
        
        Args:
            page_num: Page number to extract
            document_id: Document identifier for correlation
        
        Returns:
            Markdown-formatted text for the page
        """
        if page_num not in self.pages:
            return ""
        
        page_blocks = self.pages[page_num]
        output_lines = []
        
        # Add page header with correlation metadata
        output_lines.append(f"# Page {page_num}")
        output_lines.append("")
        output_lines.append(f"<!-- DOCUMENT_ID: {document_id} -->")
        output_lines.append(f"<!-- PAGE_NUMBER: {page_num} -->")
        output_lines.append(f"<!-- IMAGE_FILE: page_{page_num}.png -->")
        output_lines.append("")
        
        # Calculate average confidence for page
        confidences = [
            block.get('Confidence', 100)
            for block in page_blocks
            if 'Confidence' in block
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 100
        output_lines.append(f"<!-- CONFIDENCE: {avg_confidence:.1f}% -->")
        output_lines.append("")
        
        # Process LINE blocks (main text content)
        for block in page_blocks:
            if block['BlockType'] == 'LINE':
                text = self._get_block_text(block)
                if text.strip():
                    output_lines.append(text)
                    output_lines.append("")
            
            # Process TABLE blocks
            elif block['BlockType'] == 'TABLE':
                table_text = self._extract_table_text(block)
                if table_text:
                    output_lines.append(f"<!-- TABLE: {block['Id']} -->")
                    output_lines.append(table_text)
                    output_lines.append("")
        
        return "\n".join(output_lines)
    
    def get_page_count(self) -> int:
        """Get total number of pages."""
        return len(self.pages)
    
    def get_page_numbers(self) -> List[int]:
        """Get list of page numbers."""
        return sorted(self.pages.keys())


def load_textract_from_s3(bucket: str, key: str) -> Dict[str, Any]:
    """
    Load Textract JSON output from S3.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
    
    Returns:
        Textract JSON data
    """
    s3_client = boto3.client('s3')
    response = s3_client.get_object(Bucket=bucket, Key=key)
    data = json.loads(response['Body'].read())
    return data


def load_page_images_from_s3(
    bucket: str,
    prefix: str,
    local_dir: Path
) -> Dict[int, Path]:
    """
    Download page images from S3 to local directory.
    
    Args:
        bucket: S3 bucket name
        prefix: S3 prefix for page images (e.g., 'document_id/pages/')
        local_dir: Local directory to save images
    
    Returns:
        Dict mapping page number to local image path
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    
    s3_client = boto3.client('s3')
    
    # List all images in the prefix
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    
    if 'Contents' not in response:
        logger.warning(f"No images found at s3://{bucket}/{prefix}")
        return {}
    
    image_map = {}
    for obj in response['Contents']:
        key = obj['Key']
        # Skip if it's just the prefix (directory)
        if key == prefix or key.endswith('/'):
            continue
        
        # Extract page number from filename (e.g., page_1.png -> 1)
        filename = Path(key).name
        try:
            # Handle both page_1.png and page_001.png formats
            page_num_str = filename.replace('page_', '').replace('.png', '')
            page_num = int(page_num_str)
        except ValueError:
            logger.warning(f"Could not extract page number from {filename}")
            continue
        
        # Download image
        local_path = local_dir / filename
        
        logger.info(f"Downloading {key} to {local_path}")
        s3_client.download_file(bucket, key, str(local_path))
        image_map[page_num] = local_path
    
    return image_map


def load_document_pages_for_cognee(
    document_id: str,
    textract_s3_bucket: str,
    textract_s3_key: str,
    page_images_s3_bucket: Optional[str] = None,
    page_images_s3_prefix: Optional[str] = None,
    local_text_dir: Optional[Path] = None,
    local_images_dir: Optional[Path] = None
) -> Tuple[List[Tuple[Path, Optional[Path]]], int]:
    """
    Load document data as page pairs for Cognee multi-modal ingestion.
    
    This creates explicit text-image correlation by:
    1. Splitting text into separate files per page
    2. Pairing each text file with its corresponding image
    3. Adding metadata linking them together
    
    Args:
        document_id: Unique document identifier
        textract_s3_bucket: S3 bucket containing Textract JSON
        textract_s3_key: S3 key for Textract JSON
        page_images_s3_bucket: S3 bucket containing page images (optional)
        page_images_s3_prefix: S3 prefix for page images (optional)
        local_text_dir: Local directory for text files (optional)
        local_images_dir: Local directory for images (optional)
    
    Returns:
        Tuple of (list of (text_path, image_path) pairs, total_pages)
    """
    # Set default directories
    if local_text_dir is None:
        local_text_dir = Path(f"temp_pages/{document_id}")
    if local_images_dir is None:
        local_images_dir = Path(f"temp_images/{document_id}")
    
    local_text_dir.mkdir(parents=True, exist_ok=True)
    
    # Load and extract Textract data
    logger.info(f"Loading Textract data from s3://{textract_s3_bucket}/{textract_s3_key}")
    textract_data = load_textract_from_s3(textract_s3_bucket, textract_s3_key)
    
    blocks = textract_data.get('Blocks', [])
    extractor = TextractPageExtractor(blocks)
    
    total_pages = extractor.get_page_count()
    logger.info(f"Document has {total_pages} pages")
    
    # Load page images if available
    image_map = {}
    if page_images_s3_bucket and page_images_s3_prefix:
        logger.info(f"Loading page images from s3://{page_images_s3_bucket}/{page_images_s3_prefix}")
        image_map = load_page_images_from_s3(
            page_images_s3_bucket,
            page_images_s3_prefix,
            local_images_dir
        )
        logger.info(f"Downloaded {len(image_map)} page images")
    
    # Create page pairs
    page_pairs = []
    for page_num in extractor.get_page_numbers():
        # Extract and save page text
        page_text = extractor.extract_page_text(page_num, document_id)
        text_file = local_text_dir / f"page_{page_num:03d}.md"
        text_file.write_text(page_text)
        
        # Get corresponding image (if available)
        image_file = image_map.get(page_num)
        
        page_pairs.append((text_file, image_file))
        
        # Log progress
        if image_file:
            logger.info(f"Created page pair {page_num}/{total_pages}: {text_file.name} + {image_file.name}")
        else:
            logger.info(f"Created page text {page_num}/{total_pages}: {text_file.name} (no image)")
    
    return page_pairs, total_pages
