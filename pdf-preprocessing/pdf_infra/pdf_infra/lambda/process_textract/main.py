'''
process_textract
----------------

Lambda function processing a Textract job. Once a textract job is processed, the output of 
the job is written on S3 and a message is push on a SQS queue with the output location.

Textract publishes its job status on a SNS topic. This Lambda function is trigger by a 
SNS topic used by Textract.

Requirements
------------
* SNS push event from a topic used by Textract to publish its job status.
* Env variables:
    * LOG_LEVEL (optional): the log level of the lambda function .
    * DDB_DOCUMENTS_TABLE: a dynamoDB table for logging.
    * TEXTRACT_RES_QUEUE_URL: SQS queue used to publish the S3 location of Textract outputs.
    * REGION: the region of the of the SQS queue defined by TEXTRACT_RES_QUEUE_URL.
'''
# import modules
# --------------
# standard modules
import boto3
import json
import logging
import os
import time

from datetime import datetime

# custom modules from layers
from textracttools import TextractParser, save_json_to_s3
from helpertools import ProcessingDdbTable, get_logger, create_throttled_textract_client

# X-Ray tracing imports
from xraysdk import (
    capture_lambda_handler, 
    setup_correlation_context, 
    add_document_annotations,
    add_s3_annotations,
    add_dynamodb_annotations,
    add_textract_annotations,
    add_processing_metadata,
    propagate_correlation_id
)

# typing
from typing import Dict

# logger
# ------ 
#If no LOG_LEVEL env var or wrong LOG_LEVEL env var, fallback 
# to INFO log level
logger = get_logger(os.getenv('LOG_LEVEL', default='INFO'))


    # lambda entrypoint
    # -----------------
@capture_lambda_handler
def lambda_handler(event, context):
    logger.info('event: {}'.format(event))

    # Setup correlation context for tracing
    setup_correlation_context(event, 'process_textract')

    args = parse_args(event)
    logger.info('Processing Textract mode: {}'.format(args['textract_mode']))

    # Get AWS connectors. Use modern SQS endpoint format that works for both 
    # commercial and GovCloud regions
    ddb_doc_table = ProcessingDdbTable(args['ddb_documents_table'])
    sqs_endpoint_url = f"https://sqs.{args['region']}.amazonaws.com"
    sqs_client = boto3.client('sqs', endpoint_url=sqs_endpoint_url)

    # for each job (generally, only one per sns message):
    # 1. get the textract blocks
    # 2. save the blocks to S3
    # 4. send back the token to the step function
    returnDict = dict()
    returnDict['textract_jobs'] = list()
    tt_bucket = args['textract_bucket']
    logger.info('nb of textract jobs: {}'.format(len(args['textract_jobs'])))
    
    for t,tt_job in enumerate(args['textract_jobs']):
        start_time = time.time()
        logger.info(f"processing Textract job {t} of {len(args['textract_jobs'])}")

        document_id = tt_job['job_tag']
        document_name = tt_job['original_document']['key'].split('/')[-1]
        logger.info('document_id: {}'.format(document_id))
        logger.info(f"document bucket: {tt_job['original_document']['bucket']}")
        logger.info(f"document key: {tt_job['original_document']['key']}")
        
        # Set up correlation context with document ID
        setup_correlation_context({'document_id': document_id}, 'process_textract')
        
        # Add document annotations for X-Ray tracing
        add_document_annotations(
            document_id=document_id,
            document_name=document_name,
            bucket=tt_job['original_document']['bucket'],
            key=tt_job['original_document']['key'],
            processing_stage='process_textract'
        )
        
        # Add Textract job annotations
        add_textract_annotations(
            job_id=tt_job['job_id'],
            job_status=tt_job['status']
        )

        # store info about textract job end in DynamoDB
        try:
            ddb_doc_table.update_item(
                doc_id=document_id, 
                doc_name=document_name,
                key='textract_async_end',
                value={'datetime': tt_job['end_datetime'].strftime('%Y-%m-%dT%H:%M:%S') + '+00:00'},
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

        # get the blocks and save them to s3
        try:
            blocks = TextractParser.get_textract_result_blocks(tt_job['job_id'], args['textract_mode'])
            
            # Calculate metrics from blocks
            block_count = len(blocks.get('Blocks', []))
            word_count = sum(1 for block in blocks.get('Blocks', []) if block.get('BlockType') == 'WORD')
            page_count = sum(1 for block in blocks.get('Blocks', []) if block.get('BlockType') == 'PAGE')
            
            # Calculate average confidence
            confidences = [block.get('Confidence', 0) for block in blocks.get('Blocks', []) if 'Confidence' in block]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            # Update Textract annotations with detailed metrics
            add_textract_annotations(
                job_id=tt_job['job_id'],
                job_status=tt_job['status'],
                page_count=page_count,
                block_count=block_count,
                word_count=word_count,
                confidence_score=avg_confidence / 100.0 if avg_confidence > 1 else avg_confidence
            )
            
            tt_output_key = os.path.join(document_id, 'textract_output_blocks.json')
            save_json_to_s3(tt_bucket, tt_output_key, blocks)
            
            # Add S3 operation annotation
            add_s3_annotations(
                operation='put_object',
                bucket=tt_bucket,
                key=tt_output_key,
                success=True
            )
            
        except Exception as e:
            add_s3_annotations(
                operation='put_object',
                bucket=tt_bucket,
                key=tt_output_key if 'tt_output_key' in locals() else 'unknown',
                success=False
            )
            logger.error(f"Failed to process Textract blocks: {e}")
            raise

        # package the job info into a dict for returns and for the textract results SQS
        tt_job_info = {
            'document_id': document_id,
            'document_name': document_name,
            'original_document_s3': {
                'bucket': tt_job['original_document']['bucket'],
                'key': tt_job['original_document']['key'],
            },
            'textract_output_s3': {
                'bucket': tt_bucket,
                'key': tt_output_key
            }
        }
        
        # Propagate correlation ID in the message
        tt_job_info = propagate_correlation_id(tt_job_info)
        
        returnDict['textract_jobs'].append(tt_job_info)

        try:
            sqs_client.send_message(
                QueueUrl=args['textract_res_queue_url'],
                MessageBody=json.dumps(tt_job_info),
            )
            
            # Add SQS operation annotation
            from xraysdk.annotations import add_performance_annotations
            add_performance_annotations()
            
        except Exception as ex:
            logger.error(f"Cannot send message to SQS queue {args['textract_res_queue_url']}")
            add_processing_metadata(
                stage='sqs_send',
                status='error',
                error_message=str(ex)
            )
            raise ex
        
        # Add processing metadata
        end_time = time.time()
        add_processing_metadata(
            stage='process_textract',
            start_time=start_time,
            end_time=end_time,
            status='success',
            metrics={
                'textract_job_id': tt_job['job_id'],
                'block_count': block_count if 'block_count' in locals() else 0,
                'word_count': word_count if 'word_count' in locals() else 0,
                'page_count': page_count if 'page_count' in locals() else 0,
                'avg_confidence': avg_confidence if 'avg_confidence' in locals() else 0
            }
        )


    return {
        'statusCode': 200,
        'body': json.dumps(returnDict)
    }


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
    args = dict()

    # get arguments from the sns payload sent by textract at the end of the job
    args['textract_jobs'] = list()
    for record in event['Records']:
        message = json.loads(record['Sns']['Message'])
        # store the textract infos
        args['textract_jobs'].append({
            'job_id': message['JobId'],
            'status': message['Status'],
            'job_tag': message['JobTag'],
            'end_datetime': datetime.utcfromtimestamp(message['Timestamp']/1000),
            'original_document': {
                'bucket': message['DocumentLocation']['S3Bucket'],
                'key': message['DocumentLocation']['S3ObjectName'],
            }
        })

    # get the environement variables
    args['log_level'] = os.getenv('LOG_LEVEL', default='INFO')
    args['region'] = os.getenv('REGION')
    args['ddb_documents_table'] = os.getenv('DDB_DOCUMENTS_TABLE')
    args['textract_bucket'] = os.getenv('TEXTRACT_BUCKET')
    args['textract_res_queue_url'] = os.getenv('TEXTRACT_RES_QUEUE_URL')
    args['textract_mode'] = os.getenv('TEXTRACT_MODE', default='TEXT').upper()

    return args
