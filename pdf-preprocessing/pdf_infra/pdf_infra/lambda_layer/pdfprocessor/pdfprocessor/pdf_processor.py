# import modules
# --------------
import os
import logging
import math
import boto3
import fitz  #This is PyMuPdf
from helpertools import BoundingBox
from io import StringIO
import sys
import datetime
# typing
from typing import Dict, Optional, List, Any

# preparation
# -----------
# get the root logger
logger = logging.getLogger()


# Rasterization-focused helper functions only
# ---------------------------------------------


# Font handling removed - now using rasterization only for visual preservation


def fitz_open_without_fonts():
    """Open a fitz document with font-related configurations disabled for safer testing"""
    # Create temporary document with minimal font dependencies
    doc = fitz.open()
    # Disable font embedding to avoid font-related errors during testing
    doc.set_metadata({"name": "temp", "creator": "font-test"})
    return doc


def detect_page_dpi(page: fitz.Page) -> int:
    """
    Detect the effective DPI (dots per inch) of a PDF page by analyzing embedded images.
    Returns the estimated DPI to preserve original image size and quality during rasterization.

    For scanned PDFs, embedded images represent the original scan resolution.
    This function calculates the effective DPI to match during re-rasterization.
    """
    try:
        # Get all embedded images on the page
        page_images = page.get_images()

        if not page_images:
            # No embedded images found - estimate based on page size for scanned documents
            page_width = page.rect.width  # in points
            page_height = page.rect.height
            # Assume standard letter size if within typical ranges, estimate 300 DPI
            if 400 < page_width < 700 and 500 < page_height < 900:  # Approx letter size in points
                return 300
            else:
                return 150  # Conservative default for unknown pages

        dpi_values = []

        for img_info in page_images:
            try:
                # Get image dimensions
                # img_info format: (xref, isize, bpc, colorspace, w, h, type, ext)
                if len(img_info) >= 6:
                    width_pixels = img_info[4]
                    height_pixels = img_info[5]

                    # Get bounding box where image is displayed on the page
                    img_rect = page.get_image_bbox(img_info[7]) if len(img_info) > 7 else None

                    if img_rect:
                        # Calculate effective DPI
                        # PDF coordinate system: 72 points = 1 inch
                        width_inches = img_rect.width / 72.0
                        height_inches = img_rect.height / 72.0

                        # Calculate DPI for both dimensions
                        dpi_x = width_pixels / width_inches if width_inches > 0 else 0
                        dpi_y = height_pixels / height_inches if height_inches > 0 else 0

                        # Use the average of both dimensions
                        if dpi_x > 0 and dpi_y > 0:
                            effective_dpi = (dpi_x + dpi_y) / 2
                            dpi_values.append(effective_dpi)
                    else:
                        # If no bounding box info, try alternative method
                        # Get the transform matrix from the page's image command
                        xref = img_info[0]
                        pix = fitz.Pixmap(page.doc, xref)
                        if pix.width > 0 and pix.height > 0:
                            # Estimate based on page height (rough approximation)
                            approx_dpi = (pix.height / page.rect.height) * 72
                            dpi_values.append(approx_dpi)
                        pix = None  # Clean up

            except Exception as e:
                logger.debug(f"Failed to analyze image {img_info}: {e}")
                continue

        if dpi_values:
            # Use median DPI to be robust against outliers
            sorted_dpis = sorted(dpi_values)
            median_dpi = sorted_dpis[len(sorted_dpis) // 2]

            # Clamp to reasonable range (72-400 DPI)
            clamped_dpi = max(72, min(400, median_dpi))
            return int(clamped_dpi)

        else:
            # Fallback estimation based on page size
            return 150

    except Exception as e:
        logger.warning(f"DPI detection failed: {e}, using fallback")
        return 150  # Safe default


# All text replacement functions removed - rasterization only

# original functions (preserved for rasterization fallback)
# -------------------------------------------------------

def make_pdf_doc_searchable_rasterized(
    pdf_doc: fitz.Document,
    textract_blocks: List[Dict[str, Any]],
    add_word_bbox: bool=False,
    show_selectable_char: bool=False,
    pdf_image_dpi: int=120,
    pdf_color_space: str='GRAY',
    verbose: bool=False,
    save_page_images: bool=False,
    page_images_bucket: Optional[str]=None,
    page_images_prefix: Optional[str]=None,
) -> fitz.Document:
    """
    Rasterization-based PDF processing with optional page image saving.
    
    Args:
        pdf_doc: Input PDF document
        textract_blocks: Textract OCR blocks for text overlay
        add_word_bbox: Draw bounding boxes around words
        show_selectable_char: Make text visible (for debugging)
        pdf_image_dpi: Default DPI for rasterization
        pdf_color_space: 'RGB' or 'GRAY'
        verbose: Enable verbose logging
        save_page_images: If True, save page images to S3 for multi-modal processing
        page_images_bucket: S3 bucket for page images (required if save_page_images=True)
        page_images_prefix: S3 key prefix for page images (e.g., 'document_id/pages/')
    
    Returns:
        Processed PDF document with searchable text overlay
    """

    old_stderr = sys.stderr
    sys.stderr = stderr_string = StringIO()

    # save the pages as images (jpg) and bundle these images into a pdf document (pdf_doc_img)
    try:
        # Determine colorspace based on parameter
        colorspace = fitz.csRGB if pdf_color_space.upper() == 'RGB' else fitz.csGRAY

        pdf_doc_img = fitz.open()
        for ppi,pdf_page in enumerate(pdf_doc.pages()):
            # Dynamically detect DPI for this page to preserve original quality
            detected_dpi = detect_page_dpi(pdf_page)
            effective_dpi = detected_dpi if detected_dpi > 0 else pdf_image_dpi

            if verbose:
                logger.info(f"Page {ppi + 1}: Using DPI {effective_dpi} (detected: {detected_dpi})")

            # Use dynamic DPI while maintaining configurable color space
            pdf_pix_map = pdf_page.get_pixmap(dpi=effective_dpi, colorspace=colorspace)
            
            # NEW: Save page image to S3 for multi-modal processing (Cognee)
            if save_page_images and page_images_bucket and page_images_prefix:
                page_number = ppi + 1
                image_key = f"{page_images_prefix}page_{page_number}.png"
                try:
                    save_page_image_to_s3(
                        pixmap=pdf_pix_map,
                        bucket=page_images_bucket,
                        key=image_key,
                        image_format='png'
                    )
                    if verbose:
                        logger.info(f"Saved page {page_number} image to S3: {image_key}")
                except Exception as e:
                    logger.warning(f"Failed to save page {page_number} image to S3: {e}")
                    # Continue processing even if image save fails
            
            pdf_page_img = pdf_doc_img.new_page(width=pdf_page.rect.width, height=pdf_page.rect.height)

            # Insert image (compression handled at PDF save level)
            xref = pdf_page_img.insert_image(
                rect=pdf_page.rect,
                pixmap=pdf_pix_map
            )
        # Note: Keeping original pdf_doc open for later use in main.py

        # add the searchable character to the image PDF and bounding boxes if required by user
        print_step = 1000
        bbox_color = (220/255, 20/255, 60/255) #red-ish color
        fontsize_initial = 15
        for blocki,block in enumerate(textract_blocks):
            if verbose:
                if blocki%print_step == 0:
                    logger.info(
                        (f'processing blocks {blocki} to {blocki+print_step} out of {len(textract_blocks)} blocks')
                    )
            if block['BlockType']=='WORD':
                # get the page object
                page = block['Page']-1 #zero-counting
                pdf_page = pdf_doc_img[page]
                # get the bbox object and scale it to the page pixel size
                bbox = BoundingBox.from_textract_bbox(block['Geometry']['BoundingBox'])
                bbox.scale(pdf_page.rect.width, pdf_page.rect.height)

                # draw a bbox around each word
                if add_word_bbox:
                    pdf_rect  = fitz.Rect(bbox.left, bbox.top, bbox.right, bbox.bottom)
                    pdf_page.draw_rect(
                        pdf_rect,
                        color = bbox_color,
                        fill = None,
                        width = 0.7,
                        dashes = None,
                        overlay = True,
                        morph = None
                    )

                # add some text next to the bboxs
                fill_opacity = 1 if show_selectable_char else 0
                text = block['Text']
                text_length = fitz.get_text_length(text, fontname='helv', fontsize=fontsize_initial)
                fontsize_optimal = int(math.floor((bbox.width/text_length)*fontsize_initial))
                rc = pdf_page.insert_text(
                    point=fitz.Point(bbox.left, bbox.bottom),  # bottom-left of 1st char
                    text=text,
                    fontname = 'helv',  # the default font
                    fontsize = fontsize_optimal,
                    rotate = 0,
                    color = bbox_color,
                    fill_opacity=fill_opacity
                )
    except Exception as e:
        logger.error(f"Error in rasterization-based PDF processing: {e}")
        return None

    sys.stderr = old_stderr
    if "MuPDF error: syntax error: unknown keyword" in stderr_string.getvalue():
        logger.error("MuPDF error: syntax error: unknown keyword")
        return None
    return pdf_doc_img


def make_pdf_doc_searchable(
    pdf_doc: fitz.Document,
    textract_blocks: List[Dict[str, Any]],
    force_rasterization: bool = True,  # Always true now
    add_word_bbox: bool=False,
    show_selectable_char: bool=False,
    pdf_image_dpi: int=120,
    pdf_color_space: str='GRAY',
    verbose: bool=False,
    save_page_images: bool=False,
    page_images_bucket: Optional[str]=None,
    page_images_prefix: Optional[str]=None,
) -> fitz.Document:
    """
    PDF processing using rasterization for visual preservation.

    Always uses rasterization to maintain exact original visual appearance
    while adding searchable text overlay. Optionally saves page images to S3
    for multi-modal processing with Cognee.
    
    Args:
        pdf_doc: Input PDF document
        textract_blocks: Textract OCR blocks for text overlay
        force_rasterization: Always True (kept for compatibility)
        add_word_bbox: Draw bounding boxes around words
        show_selectable_char: Make text visible (for debugging)
        pdf_image_dpi: Default DPI for rasterization
        pdf_color_space: 'RGB' or 'GRAY'
        verbose: Enable verbose logging
        save_page_images: If True, save page images to S3 for multi-modal processing
        page_images_bucket: S3 bucket for page images
        page_images_prefix: S3 key prefix for page images (e.g., 'document_id/pages/')
    
    Returns:
        Processed PDF document with searchable text overlay
    """
    logger.info("Processing PDF with rasterization for visual preservation")

    return make_pdf_doc_searchable_rasterized(
        pdf_doc=pdf_doc,
        textract_blocks=textract_blocks,
        add_word_bbox=add_word_bbox,
        show_selectable_char=show_selectable_char,
        pdf_image_dpi=pdf_image_dpi,
        pdf_color_space=pdf_color_space,
        verbose=verbose,
        save_page_images=save_page_images,
        page_images_bucket=page_images_bucket,
        page_images_prefix=page_images_prefix
    )


def load_pdf_from_s3(bucket: str, key: str) -> fitz.Document:
    '''
    Read a PDF document from S3 and load it into a fitz.Document object. Fitz is 
    part of the module PyMuPDF
    '''
    s3_res = boto3.resource('s3')
    s3_object = s3_res.Object(bucket, key)
    fs = s3_object.get()['Body'].read()
    pdf_doc = fitz.open(stream=fs, filetype='pdf')
    return pdf_doc


def save_page_image_to_s3(
    pixmap: fitz.Pixmap,
    bucket: str,
    key: str,
    image_format: str = 'png'
) -> Dict[str, Any]:
    '''
    Save a PyMuPDF Pixmap (page image) to S3 as PNG or JPEG.
    
    Args:
        pixmap: fitz.Pixmap object containing the page image
        bucket: S3 bucket name
        key: S3 object key (path)
        image_format: 'png' or 'jpeg' (default: 'png' for lossless quality)
    
    Returns:
        S3 put_object response
    '''
    s3_client = boto3.client('s3')
    
    # Convert pixmap to bytes based on format
    if image_format.lower() == 'jpeg':
        image_bytes = pixmap.tobytes(output='jpeg', jpg_quality=95)
        content_type = 'image/jpeg'
    else:  # Default to PNG
        image_bytes = pixmap.tobytes(output='png')
        content_type = 'image/png'
    
    # Upload to S3
    response = s3_client.put_object(
        Body=image_bytes,
        Bucket=bucket,
        Key=key,
        ContentType=content_type,
        Metadata={
            'width': str(pixmap.width),
            'height': str(pixmap.height),
            'dpi': str(pixmap.xres) if hasattr(pixmap, 'xres') else 'unknown',
            'created': str(datetime.datetime.now().isoformat())
        }
    )
    
    logger.info(f"Saved page image to s3://{bucket}/{key} ({len(image_bytes)} bytes)")
    return response


def save_pdf_to_s3(pdf_doc:fitz.Document, bucket: str, key: str, compression_level: int = 9) -> Dict[str, Any]:
    '''
    Save a fitz.Document object (i.e. a PDF in PyMuPDF module) directly to S3 without
    passing via a local save to disk. Returns the response from S3.
    Enhanced with aggressive compression optimization for rasterized PDFs.
    '''
    s3_client = boto3.client('s3')

    # Aggressive compression for rasterized PDFs to minimize size
    response = s3_client.put_object(
        Body=pdf_doc.tobytes(
            garbage=4,     # High garbage collection - removes more unused objects
            clean=True,    # Clean document structure
            deflate=True,  # Enable deflate compression
            deflate_images=True,  # Compress images with maximum effort
            deflate_fonts=True,    # Compress fonts
            expand=0,      # Don't expand streams (keeps compression)
            pretty=False,  # Remove pretty formatting for smaller size
            ascii=False,   # Use binary mode for efficiency
            linear=False,  # Don't linearize (web optimization) - reduces size
            no_new_id=False  # Keep IDs optimized
        ),
        Bucket=bucket,
        Key=key,
        # Add compression metadata
        Metadata={
            'compression': 'optimized',
            'original-size': str(len(pdf_doc.tobytes())),
            'compression-date': str(datetime.datetime.now().isoformat())
        }
    )
    return response
