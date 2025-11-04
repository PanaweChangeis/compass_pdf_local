'''
process_pdf
-----------

Add selectable characters to a PDF. Useful to convert a scanned PDF (i.e. pixel 
characters) into a selectable PDF.

Algorithm:
1. load the original PDF from S3 (S3 location info located in the SQS message)
2. read all the page and convert them to image. Indeed, the original PDF might already 
   have selectable characters, or a mix with pixel characters, so we want to avoid 
   overlay characters on characters
3. Add the character to the images. the characters are added by words. For each word, 
   the best fitting fontsize is computed to the character word length equal the pixel 
   word length
4. Save the pdf with selectable characters

Required environment variable
* OUTPUT_BUCKET
* DDB_DOCUMENTS_TABLE
'''

# import modules
# --------------
# standard modules
import os
import boto3
import json
import datetime
import time
import subprocess
import shutil

# boto3 transfer configuration for multi-part uploads
from boto3.s3.transfer import TransferConfig

# custom modules from layers
from textracttools import load_json_from_s3
from helpertools import (
    ProcessingDdbTable,
    get_logger
)
from pdfprocessor import (
    load_pdf_from_s3, 
    save_pdf_to_s3, 
    make_pdf_doc_searchable
)

# X-Ray tracing imports
from xraysdk import (
    capture_lambda_handler, 
    setup_correlation_context, 
    add_document_annotations,
    add_s3_annotations,
    add_dynamodb_annotations,
    add_processing_metadata,
    add_performance_annotations
)

# typing
from typing import Dict, Optional


# logger
# ------
#If no LOG_LEVEL env var or wrong LOG_LEVEL env var, fallback
# to INFO log level
logger = get_logger(os.getenv('LOG_LEVEL', default='INFO'))


# disk space monitoring functions
# -------------------------------
def get_disk_usage(path='/tmp'):
    """Get disk usage statistics for the given path."""
    stat = os.statvfs(path)
    total = stat.f_bsize * stat.f_blocks
    free = stat.f_bsize * stat.f_bavail
    used = total - free
    usage_percent = (used / total) * 100 if total > 0 else 0
    return {
        'total_mb': total / (1024 * 1024),
        'free_mb': free / (1024 * 1024),
        'used_mb': used / (1024 * 1024),
        'usage_percent': usage_percent
    }


def cleanup_temp_files():
    """Clean up temporary files in /tmp directory."""
    temp_dir = '/tmp'
    try:
        for filename in os.listdir(temp_dir):
            file_path = os.path.join(temp_dir, filename)
            if filename.startswith('compressed_') or os.path.isfile(file_path):
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        logger.info(f"Cleaned up temp file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove {file_path}: {e}")
        logger.info("Temp file cleanup completed")
    except Exception as e:
        logger.error(f"Error during temp file cleanup: {e}")


# lambda entrypoint
# -----------------
@capture_lambda_handler
def lambda_handler(event, context):
    logger.info('event: {}'.format(event))
    
    # Setup correlation context for tracing
    setup_correlation_context(event, 'selectable_pdf')
    
    args = parse_args(event)
    logger.info(f'args: {args}')

    # Get ddb table
    ddb_doc_table = ProcessingDdbTable(args['ddb_documents_table'])

    # build the final sns topic if required
    if args['final_sns_topic_arn']:
        sns_ress = boto3.resource('sns')
        final_sns_topic = sns_ress.Topic(args['final_sns_topic_arn'])

    returns = list()
    for rec in args['records']:
        start_time = time.time()
        document_id = rec['document_id']
        document_name = rec['document_name']
        logger.info('document id: {}'.format(document_id))
        logger.info('document name: {}'.format(document_name))
        logger.info(f"document bucket: {rec['original_document_s3']['bucket']}")
        logger.info(f"document key: {rec['original_document_s3']['key']}")
        
        # Set up correlation context with document ID
        setup_correlation_context({'document_id': document_id}, 'selectable_pdf')
        
        # Add document annotations for X-Ray tracing
        add_document_annotations(
            document_id=document_id,
            document_name=document_name,
            bucket=rec['original_document_s3']['bucket'],
            key=rec['original_document_s3']['key'],
            processing_stage='selectable_pdf'
        )

        # store info about starting creating the selectable PDF document
        try:
            ddb_doc_table.update_item(
                doc_id=document_id, 
                doc_name=document_name,
                key='selectable_pdf',
                value={'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S') + '+00:00'},
                add_logging_datetime=False
            )
            
            # Add DynamoDB operation annotation
            add_dynamodb_annotations(
                operation='update_item',
                table_name=args['ddb_documents_table'],
                item_count=1,
                success=True
            )
            
        except Exception as e:
            add_dynamodb_annotations(
                operation='update_item',
                table_name=args['ddb_documents_table'],
                success=False
            )
            logger.error(f"Failed to update DDB item: {e}")
            raise

        # process the PDF
        try:
            # Clean up any leftover temp files from previous runs
            logger.info("Starting temp file cleanup")
            cleanup_temp_files()

            # Load original PDF
            pdf_doc = load_pdf_from_s3(rec['original_document_s3']['bucket'], rec['original_document_s3']['key'])

            # Add S3 operation annotation for loading original PDF
            add_s3_annotations(
                operation='get_object',
                bucket=rec['original_document_s3']['bucket'],
                key=rec['original_document_s3']['key'],
                success=True
            )

            # Log initial disk usage
            initial_disk_stats = get_disk_usage()
            logger.info(f"Initial disk usage: {initial_disk_stats['usage_percent']:.1f}% ({initial_disk_stats['used_mb']:.1f}MB/{initial_disk_stats['total_mb']:.1f}MB)")

        except Exception as e:
            add_s3_annotations(
                operation='get_object',
                bucket=rec['original_document_s3']['bucket'],
                key=rec['original_document_s3']['key'],
                success=False
            )
            logger.error(f"Failed to load PDF from S3: {e}")
            raise
        
        try:
            # Load Textract blocks
            textract_blocks = load_json_from_s3(rec['textract_output_s3']['bucket'], rec['textract_output_s3']['key'])
            textract_blocks = textract_blocks['Blocks']
            logger.info(f'nb blocks: {len(textract_blocks)}')

            # Calculate metrics
            num_word_blocks = sum(1 for blk in textract_blocks if blk.get('BlockType') == 'WORD')
            num_page_blocks = sum(1 for blk in textract_blocks if blk.get('BlockType') == 'PAGE')
            logger.info(f'number of WORD blocks {num_word_blocks}')

            # Early debug: Log PDF details while document is still open
            try:
                logger.info(f"Original PDF has {len(pdf_doc)} pages, {num_word_blocks} word blocks")
            except ValueError as e:
                logger.warning(f"Could not get PDF page count: {e}")
            
            # Add S3 operation annotation for loading Textract output
            add_s3_annotations(
                operation='get_object',
                bucket=rec['textract_output_s3']['bucket'],
                key=rec['textract_output_s3']['key'],
                success=True
            )
            
            # Add Textract annotations
            from xraysdk.annotations import add_textract_annotations
            add_textract_annotations(
                job_id='processed',  # Job already completed
                job_status='SUCCEEDED',
                page_count=num_page_blocks,
                block_count=len(textract_blocks),
                word_count=num_word_blocks
            )
            
        except Exception as e:
            add_s3_annotations(
                operation='get_object',
                bucket=rec['textract_output_s3']['bucket'],
                key=rec['textract_output_s3']['key'],
                success=False
            )
            logger.error(f"Failed to load Textract blocks from S3: {e}")
            raise

        try:
            # Process PDF to make it searchable - smart strategy selection
            # Enable page image saving for multi-modal Cognee processing
            selectable_pdf_doc = make_pdf_doc_searchable(
                pdf_doc=pdf_doc,
                textract_blocks=textract_blocks,
                force_rasterization=args['force_rasterization'],
                add_word_bbox=args['add_word_bbox'],
                show_selectable_char=args['show_character'],
                pdf_image_dpi=args['pdf_image_dpi'],
                pdf_color_space=args['pdf_color_space'],
                verbose=True,
                save_page_images=True,  # Enable page image saving
                page_images_bucket=args['output_bucket'],  # Same bucket as output
                page_images_prefix=f"{document_id}/pages/"  # Store in pages/ subdirectory
            )

            if selectable_pdf_doc is not None:
                output_key = f"{document_id}/{document_name}"
                uncompressed_key = f"{document_id}/uncompressed_{document_name}"
                temp_path = f"/tmp/{document_name}"

                # Debug: Log disk usage before saving
                pre_save_disk = get_disk_usage()
                logger.info(f"Pre-save disk usage: {pre_save_disk['usage_percent']:.1f}% ({pre_save_disk['used_mb']:.1f}MB/{pre_save_disk['total_mb']:.1f}MB)")
                logger.info(f"Saving processed PDF to temp path: {temp_path}")

                try:
                    selectable_pdf_doc.save(temp_path)
                except Exception as e:
                    logger.error(f"Failed to save PDF: {e}")
                    post_save_disk = get_disk_usage()
                    logger.info(f"Post-save attempt disk usage: {post_save_disk['usage_percent']:.1f}% ({post_save_disk['used_mb']:.1f}MB/{post_save_disk['total_mb']:.1f}MB)")
                    raise

                # Debug: Log disk usage after saving
                post_save_disk = get_disk_usage()
                logger.info(f"Post-save disk usage: {post_save_disk['usage_percent']:.1f}% ({post_save_disk['used_mb']:.1f}MB/{post_save_disk['total_mb']:.1f}MB)")

                # Log file size
                temp_file_size = os.path.getsize(temp_path)
                temp_file_size_mb = temp_file_size / (1024 * 1024)
                logger.info(f"Searchable PDF created. File size: {temp_file_size} bytes ({temp_file_size_mb:.2f} MB)")

                # Upload uncompressed PDF to S3 (compression will be done asynchronously by Fargate)
                logger.info(f"Uploading uncompressed PDF to S3 bucket: {args['output_bucket']}, key: {uncompressed_key}")
                
                # Configure multi-part upload with threshold
                multipart_threshold_bytes = args['multipart_threshold_mb'] * 1024 * 1024
                transfer_config = TransferConfig(
                    multipart_threshold=multipart_threshold_bytes,
                    multipart_chunksize=8 * 1024 * 1024,  # 8MB chunks
                    use_threads=True,
                    max_concurrency=4
                )
                
                # Upload uncompressed PDF to S3 using multi-part upload
                s3_client = boto3.client('s3')
                s3_client.upload_file(
                    temp_path,
                    args['output_bucket'],
                    uncompressed_key,
                    Config=transfer_config
                )

                logger.info(f"Successfully uploaded uncompressed PDF to S3")

                # Clean up temp file
                os.remove(temp_path)

                # Add S3 operation annotation for saving processed PDF
                add_s3_annotations(
                    operation='put_object',
                    bucket=args['output_bucket'],
                    key=uncompressed_key,
                    success=True
                )
                
                # Trigger Step Functions workflow for async compression
                if args.get('compression_state_machine_arn'):
                    logger.info("Triggering Step Functions for async compression")
                    sfn_client = boto3.client('stepfunctions')
                    
                    # Parse subnet IDs from environment variable, filtering out empty strings
                    subnet_ids = [s.strip() for s in args.get('fargate_subnets', '').split(',') if s.strip()]
                    
                    execution_input = {
                        'document_id': document_id,
                        'document_name': document_name,
                        'input_bucket': args['output_bucket'],
                        'input_key': uncompressed_key,
                        'output_bucket': args['output_bucket'],
                        'output_key': output_key,
                        'output_final_bucket': args.get('output_final_bucket', args['output_bucket']),
                        'output_final_key': document_name,  # Clean filename without document_id prefix
                        'ddb_table': args['ddb_documents_table'],
                        'subnets': subnet_ids
                    }
                    
                    try:
                        sfn_response = sfn_client.start_execution(
                            stateMachineArn=args['compression_state_machine_arn'],
                            name=f"compress-{document_id}",
                            input=json.dumps(execution_input)
                        )
                        logger.info(f"Started Step Functions execution: {sfn_response['executionArn']}")
                    except Exception as sfn_error:
                        logger.error(f"Failed to start Step Functions execution: {sfn_error}")
                        # Don't fail the Lambda - compression can be retried manually
                else:
                    logger.warning("No compression state machine ARN configured - skipping async compression")
                
                # prepare return dict
                ret = {
                    'document_name': document_name,
                    'document_id': document_id,
                    'textract_response_s3': {
                        'bucket': rec['textract_output_s3']['bucket'],
                        'key': rec['textract_output_s3']['key']
                    },
                    'original_document_s3': {
                        'bucket': rec['original_document_s3']['bucket'],
                        'key': rec['original_document_s3']['key']
                    },
                    'processed_document_s3': {
                        'bucket': args['output_bucket'],
                        'key': output_key,
                    },
                }
                returns.append(ret)

                # send the return dict to SNS
                if args['final_sns_topic_arn']:
                    final_sns_topic.publish(Message=json.dumps(ret))
                
                # Add processing metadata
                end_time = time.time()
                add_processing_metadata(
                    stage='selectable_pdf',
                    start_time=start_time,
                    end_time=end_time,
                    status='success',
                    metrics={
                        'input_blocks': len(textract_blocks),
                        'word_blocks': num_word_blocks,
                        'page_blocks': num_page_blocks,
                        'pdf_image_dpi': args['pdf_image_dpi']
                    }
                )
                
        except subprocess.TimeoutExpired as e:
            logger.error(f"Compression timed out: {e}")
            # Handle timeout - perhaps trigger async process or fail gracefully
            raise
        except Exception as e:
            logger.error(f"ERROR making PDF ({rec['original_document_s3']['key']}) searchable: {e}")
            
            # Add error processing metadata
            end_time = time.time()
            add_processing_metadata(
                stage='selectable_pdf',
                start_time=start_time,
                end_time=end_time,
                status='error',
                error_message=str(e)
            )
            
            # Add S3 operation annotation for failed save
            add_s3_annotations(
                operation='put_object',
                bucket=args['output_bucket'],
                key=document_name,
                success=False
            )
            
            # need to trigger a rasterization of the original doc and resend through this process - without causing infinite loop if it fails again
            raise

    # return
    return_dict = {'records': returns}
    return {'statusCode': 200, 'body': return_dict}

# functions
# ---------
def parse_args(event: Dict) -> Dict:
    '''
    Parse the environment variables and the event payload (from the lambda 
    entrypoint). Process them further if required.
    
    Usage
    -----
    args = parse_args(event)
    '''
    # get args from event
    args = dict()
    args['records'] = list()
    for rec in event['Records']:
        body = json.loads(rec['body'])
        record = dict()
        record['document_id'] = body['document_id']
        record['document_name'] = body['document_name']
        record['original_document_s3'] = body['original_document_s3']
        record['textract_output_s3'] = body['textract_output_s3']
        args['records'].append(record)
    # get the environnement variables. They are the same for all records
    args['ddb_documents_table'] = os.getenv('DDB_DOCUMENTS_TABLE')
    args['output_bucket'] = os.getenv('OUTPUT_BUCKET')
    args['log_level'] = os.getenv('LOG_LEVEL', default='INFO')
    args['add_word_bbox'] = os.getenv('ADD_WORD_BBOX', default=False)
    args['show_character'] = os.getenv('SHOW_CHARACTER', default=False)
    args['pdf_image_dpi'] = os.getenv('PDF_IMAGE_DPI', default='120')  # Changed default from 200 to 120
    args['pdf_color_space'] = os.getenv('PDF_COLOR_SPACE', default='GRAY')  # New: Color space optimization
    args['final_sns_topic_arn'] = os.getenv('FINAL_SNS_TOPIC_ARN', default=None)
    args['multipart_threshold_mb'] = os.getenv('MULTIPART_THRESHOLD_MB', default='5')  # Multipart upload threshold in MB
    args['compression_state_machine_arn'] = os.getenv('COMPRESSION_STATE_MACHINE_ARN', default=None)  # Step Functions ARN for async compression
    args['fargate_subnets'] = os.getenv('FARGATE_SUBNETS', default='')  # Comma-separated subnet IDs for Fargate tasks
    args['output_final_bucket'] = os.getenv('OUTPUT_FINAL_BUCKET', default=None)  # Final output bucket for latest compressed PDFs

    # post process some environment variable (Lambda allow only strings)
    args['add_word_bbox'] = True if args['add_word_bbox'] in ['1', 'True', 'true'] else False
    args['show_character'] = True if args['show_character'] in ['1', 'True', 'true'] else False
    args['pdf_image_dpi'] = int(args['pdf_image_dpi'])
    args['multipart_threshold_mb'] = int(args['multipart_threshold_mb'])

    # Force rasterization is now always enabled for visual preservation
    args['force_rasterization'] = True

    return args
