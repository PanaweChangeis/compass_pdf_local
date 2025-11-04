"""
compress_pdf
------------

Fargate task entrypoint for compressing large PDF files using GhostScript.
This runs as a long-running task without Lambda's 15-minute timeout constraint.

Algorithm:
1. Download uncompressed PDF from S3
2. Compress using GhostScript with optimized settings
3. Upload compressed PDF back to S3 (replacing uncompressed version)
4. Update DynamoDB with completion status
5. Clean up temporary files

Required environment variables:
* INPUT_BUCKET - S3 bucket containing uncompressed PDF
* INPUT_KEY - S3 key of uncompressed PDF
* OUTPUT_BUCKET - S3 bucket for compressed PDF (usually same as input)
* OUTPUT_KEY - S3 key for compressed PDF (usually same as input)
* DDB_DOCUMENTS_TABLE - DynamoDB table name
* DOCUMENT_ID - Document ID for DynamoDB updates
* DOCUMENT_NAME - Document name for DynamoDB updates
"""

import os
import boto3
import subprocess
import datetime
import logging
import sys
from pathlib import Path
from decimal import Decimal

# X-Ray SDK imports (optional - gracefully handle if not available)
try:
    from aws_xray_sdk.core import xray_recorder, patch_all
    # Patch boto3 and other libraries for X-Ray tracing
    patch_all()
    XRAY_AVAILABLE = True
except ImportError:
    XRAY_AVAILABLE = False
    # Create a no-op decorator for when X-Ray is not available
    class NoOpRecorder:
        def capture(self, name):
            def decorator(func):
                return func
            return decorator
        def put_metadata(self, key, value):
            pass
        def put_annotation(self, key, value):
            pass
    xray_recorder = NoOpRecorder()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def get_file_size_mb(file_path):
    """Get file size in MB"""
    return os.path.getsize(file_path) / (1024 * 1024)


@xray_recorder.capture('compress_pdf_with_ghostscript')
def compress_pdf_with_ghostscript(input_path, output_path, timeout_seconds=3600):
    """
    Compress PDF using GhostScript with optimized settings.
    
    Args:
        input_path: Path to input PDF
        output_path: Path to output compressed PDF
        timeout_seconds: Maximum time to allow for compression (default 1 hour)
    
    Returns:
        dict: Compression statistics
    """
    logger.info(f"Starting GhostScript compression")
    logger.info(f"Input file: {input_path} ({get_file_size_mb(input_path):.2f} MB)")
    
    # Add X-Ray metadata
    xray_recorder.put_metadata('input_size_mb', get_file_size_mb(input_path))
    
    gs_command = [
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook",
        "-dNOPAUSE", "-dQUIET", "-dBATCH",
        "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic",
        "-dColorImageResolution=150", "-dColorImageDownsampleThreshold=2.0",
        "-dColorImageFilter=/DCTEncode",
        "-dDownsampleGrayImages=true", "-dGrayImageDownsampleType=/Bicubic",
        "-dGrayImageResolution=150", "-dGrayImageDownsampleThreshold=2.0",
        "-dGrayImageFilter=/DCTEncode",
        "-dDownsampleMonoImages=true", "-dMonoImageDownsampleType=/Bicubic",
        "-dMonoImageResolution=300", "-dMonoImageDownsampleThreshold=2.0",
        "-dMonoImageFilter=/CCITTFaxEncode",
        f"-sOutputFile={output_path}", input_path
    ]
    
    start_time = datetime.datetime.now()
    
    try:
        subprocess.run(gs_command, check=True, timeout=timeout_seconds)
        end_time = datetime.datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        input_size_mb = get_file_size_mb(input_path)
        output_size_mb = get_file_size_mb(output_path)
        compression_ratio = input_size_mb / output_size_mb if output_size_mb > 0 else 0
        
        stats = {
            'duration_seconds': duration,
            'input_size_mb': input_size_mb,
            'output_size_mb': output_size_mb,
            'compression_ratio': compression_ratio,
            'space_saved_mb': input_size_mb - output_size_mb,
            'space_saved_percent': ((input_size_mb - output_size_mb) / input_size_mb * 100) if input_size_mb > 0 else 0
        }
        
        logger.info(f"Compression completed in {duration:.1f} seconds")
        logger.info(f"Input: {input_size_mb:.2f} MB -> Output: {output_size_mb:.2f} MB")
        logger.info(f"Compression ratio: {compression_ratio:.2f}x")
        logger.info(f"Space saved: {stats['space_saved_mb']:.2f} MB ({stats['space_saved_percent']:.1f}%)")
        
        # Add X-Ray annotations for compression stats
        xray_recorder.put_annotation('compression_ratio', round(compression_ratio, 2))
        xray_recorder.put_annotation('duration_seconds', round(duration, 1))
        xray_recorder.put_metadata('compression_stats', stats)
        
        return stats
        
    except subprocess.TimeoutExpired:
        logger.error(f"GhostScript compression timed out after {timeout_seconds} seconds")
        raise
    except subprocess.CalledProcessError as e:
        logger.error(f"GhostScript compression failed: {e}")
        raise


def update_dynamodb(table_name, document_id, document_name, status, stats=None, error=None):
    """Update DynamoDB with compression status"""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    update_data = {
        'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S') + '+00:00',
        'status': status
    }
    
    if stats:
        # Convert float values to Decimal for DynamoDB compatibility
        decimal_stats = {
            key: Decimal(str(value)) if isinstance(value, float) else value
            for key, value in stats.items()
        }
        update_data['compression_stats'] = decimal_stats
    
    if error:
        update_data['error'] = str(error)
    
    try:
        table.update_item(
            Key={
                'document_id': document_id,
                'document_name': document_name
            },
            UpdateExpression='SET compression_complete = :val',
            ExpressionAttributeValues={
                ':val': update_data
            }
        )
        logger.info(f"Updated DynamoDB for document {document_id}")
    except Exception as e:
        logger.error(f"Failed to update DynamoDB: {e}")
        raise


def main():
    """Main entrypoint for Fargate task"""
    logger.info("Starting PDF compression task")
    
    # Get environment variables
    input_bucket = os.environ['INPUT_BUCKET']
    input_key = os.environ['INPUT_KEY']
    output_bucket = os.environ['OUTPUT_BUCKET']
    output_key = os.environ['OUTPUT_KEY']
    output_final_bucket = os.environ.get('OUTPUT_FINAL_BUCKET')
    output_final_key = os.environ.get('OUTPUT_FINAL_KEY')
    ddb_table = os.environ['DDB_DOCUMENTS_TABLE']
    document_id = os.environ['DOCUMENT_ID']
    document_name = os.environ['DOCUMENT_NAME']
    
    logger.info(f"Document: {document_name} (ID: {document_id})")
    logger.info(f"Input: s3://{input_bucket}/{input_key}")
    logger.info(f"Output (processed): s3://{output_bucket}/{output_key}")
    if output_final_bucket and output_final_key:
        logger.info(f"Output (final): s3://{output_final_bucket}/{output_final_key}")
    
    # Create temp directory
    temp_dir = Path('/tmp/pdf_compression')
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    input_path = temp_dir / 'input.pdf'
    output_path = temp_dir / 'output.pdf'
    
    s3_client = boto3.client('s3')
    
    try:
        # Download uncompressed PDF
        logger.info("Downloading uncompressed PDF from S3...")
        s3_client.download_file(input_bucket, input_key, str(input_path))
        logger.info(f"Downloaded {get_file_size_mb(input_path):.2f} MB")
        
        # Compress PDF
        stats = compress_pdf_with_ghostscript(str(input_path), str(output_path))
        
        # Upload compressed PDF to processed bucket (with document_id folder)
        logger.info("Uploading compressed PDF to processed bucket...")
        s3_client.upload_file(str(output_path), output_bucket, output_key)
        logger.info(f"Uploaded compressed PDF to s3://{output_bucket}/{output_key}")
        
        # Also upload to final output bucket (latest version only, clean filename)
        if output_final_bucket and output_final_key:
            logger.info("Uploading compressed PDF to final output bucket...")
            s3_client.upload_file(str(output_path), output_final_bucket, output_final_key)
            logger.info(f"Uploaded final compressed PDF to s3://{output_final_bucket}/{output_final_key}")
        
        # Update DynamoDB
        update_dynamodb(ddb_table, document_id, document_name, 'success', stats=stats)
        
        # Clean up temp files
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        logger.info("Cleaned up temporary files")
        
        logger.info("PDF compression task completed successfully")
        return 0
        
    except Exception as e:
        logger.error(f"PDF compression task failed: {e}", exc_info=True)
        
        # Update DynamoDB with error
        try:
            update_dynamodb(ddb_table, document_id, document_name, 'failed', error=str(e))
        except Exception as ddb_error:
            logger.error(f"Failed to update DynamoDB with error status: {ddb_error}")
        
        # Clean up temp files
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        
        return 1


if __name__ == '__main__':
    sys.exit(main())
