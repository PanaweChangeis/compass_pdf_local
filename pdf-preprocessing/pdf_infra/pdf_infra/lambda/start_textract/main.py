'''
start_textract
--------------

Lambda function which starts an asynchronous Textract job on a S3 PUT trigger.

Requirements
------------
* S3 PUT trigger on documents (PDFs or images)
* Env variables:
    * LOG_LEVEL (optional): the log level of the lambda function.
    * SNS_TOPIC_ARN: SNS topic used by Textract to publish the result of a job.
    * SNS_ROLE_ARN: Role used by Textract to publish job results messages on a SNS topic.
    * DDB_DOCUMENTS_TABLE: a dynamoDB table for logging.
'''
# import modules
# --------------
import json
import boto3
import os
import logging
import uuid
import random
import string
import time
from datetime import datetime, timedelta
from urllib.parse import unquote_plus

from helpertools import ProcessingDdbTable, get_logger, create_throttled_textract_client, get_job_tracker

# X-Ray tracing imports
from xraysdk import (
    capture_lambda_handler, 
    setup_correlation_context, 
    add_document_annotations,
    add_s3_annotations,
    add_dynamodb_annotations,
    add_processing_metadata
)

from typing import Dict

#If no LOG_LEVEL env var or wrong LOG_LEVEL env var, fallback 
# to INFO log level
logger = get_logger(os.getenv('LOG_LEVEL', default='INFO'))


@capture_lambda_handler
def lambda_handler(event, context):
    '''
    the event is a S3 PUT formatted as JSON. Example:
    {
        "Records": [
            {
                "eventVersion": "2.0",
                "eventSource": "aws:s3",
                "awsRegion": "eu-west-1",
                "eventTime": "1970-01-01T00:00:00.000Z",
                "eventName": "ObjectCreated:Put",
                "userIdentity": {
                    "principalId": "EXAMPLE"
                },
                "requestParameters": {
                    "sourceIPAddress": "127.0.0.1"
                },
                "responseElements": {
                    "x-amz-request-id": "EXAMPLE123456789",
                    "x-amz-id-2": "EXAMPLE123/5678abcdefghijklambdaisawesome/mnopqrstuvwxyzABCDEFGH"
                },
                "s3": {
                    "s3SchemaVersion": "1.0",
                    "configurationId": "testConfigRule",
                    "bucket": {
                        "name": "example-bucket",
                        "ownerIdentity": {
                            "principalId": "EXAMPLE"
                        },
                        "arn": "arn:aws:s3:::example-bucket"
                    },
                    "object": {
                        "key": "test/key",
                        "size": 1024,
                        "eTag": "0123456789abcdef0123456789abcdef",
                        "sequencer": "0A1B2C3D4E5F678901"
                    }
                }
            }
        ]
    }
    '''
    logger.info('event: {}'.format(event))
    
    # Setup correlation context for tracing
    setup_correlation_context(event, 'start_textract')

    # get args
    args = parse_args(event)

    # get logging table object
    ddb_doc_table = ProcessingDdbTable(args['ddb_documents_table'])

    # start textract for each s3 records. But generally, there is only one record
    responses = list()
    for r,record in enumerate(args['records']):
        start_time = time.time()
        logger.info(f"prcessing document {r} of {len(args['records'])}")
        
        # Get the document ID. This ID will follow the document during all the
        # processing. We could use `uuid.uuid1` which generates a random list
        # characters, but we want something slightly more usable, like the date +
        # a few random chars.
        document_id = generate_uid()
        logger.info(f"document ID: {document_id}")
        logger.info(f"document bucket: {record['bucket']}")
        logger.info(f"document key: {record['key']}")
        
        # Set up correlation context with document ID
        setup_correlation_context({'document_id': document_id}, 'start_textract')
        
        # Create the item in DDB
        document_name = record['key'].split('/')[-1]
        
        # Add document annotations for X-Ray tracing
        add_document_annotations(
            document_id=document_id,
            document_name=document_name,
            bucket=record['bucket'],
            key=record['key'],
            processing_stage='start_textract'
        )
        
        # Add S3 event annotations
        add_s3_annotations(
            operation='trigger_event',
            bucket=record['bucket'],
            key=record['key'],
            success=True
        )
        
        try:
            ddb_doc_table.put_item(
                doc_id=document_id, 
                doc_name=document_name,  
                item={
                    'document_id': document_id,
                    'document_name': document_name,
                    'document_s3': {
                        'bucket': record['bucket'],
                        'key': record['key'],
                    },
                    'document_put_event': {
                        'datetime': convert_datetime_s3_event(record['event_datetime']),
                        'user_id': record['event_user_id'],
                        'user_ip': record['event_user_ip']
                    }
                },
                add_logging_datetime=False
            )
            
            # Add DynamoDB operation annotation
            add_dynamodb_annotations(
                operation='put_item',
                table_name=args['ddb_documents_table'],
                item_count=1,
                success=True
            )
            
        except Exception as e:
            add_dynamodb_annotations(
                operation='put_item',
                table_name=args['ddb_documents_table'],
                success=False
            )
            logger.error(f"Failed to create DDB item: {e}")
            raise

        # Check concurrent job limits before starting new job
        job_tracker = get_job_tracker()
        if not job_tracker.can_start_new_job():
            logger.warning("Maximum concurrent Textract jobs reached, waiting for slot...")
            if not job_tracker.wait_for_job_slot(max_wait_time=120):
                logger.error("Timeout waiting for Textract job slot")
                raise Exception("Cannot start Textract job: too many concurrent jobs")

        # start textract with throttling
        try:
            logger.info('start Textract async job with throttling')
            tt_resp = textract_start_async_processing(
                input_bucket=record['bucket'],
                input_key=record['key'],
                sns_topic_arn=args['sns_topic_arn'],
                sns_role_arn=args['sns_role_arn'],
                job_tag=document_id,
                mode=args['textract_mode'],
                features=args['textract_features'],
            )
            logger.info('textract response: {}'.format(tt_resp))
            
            # Add Textract job start annotations
            from xraysdk.annotations import add_textract_annotations
            add_textract_annotations(
                job_id=tt_resp['JobId'],
                job_status='STARTED'
            )
            
        except Exception as ex:
            logger.error('Textract async job failed to start')
            add_processing_metadata(
                stage='textract_start',
                status='error',
                error_message=str(ex)
            )
            raise ex

        # add textract response to DDB
        try:
            ddb_doc_table.update_item(
                doc_id=document_id, 
                doc_name=document_name, 
                key='textract_async_start', 
                value={'job_id': tt_resp['JobId']},
                add_logging_datetime=True
            )
            
            # Add DynamoDB update annotation
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

        # Add processing metadata
        end_time = time.time()
        add_processing_metadata(
            stage='start_textract',
            start_time=start_time,
            end_time=end_time,
            status='success',
            metrics={
                'textract_job_id': tt_resp['JobId'],
                'document_size_bytes': record.get('size', 0)
            }
        )

        # build a nice return
        responses.append({
            'textract_job_id': tt_resp['JobId'],
            'original_document_id': document_id,
            'original_document': {
                'bucket': record['bucket'],
                'key': record['key'],
            }
        })

    # prepare the response dict and return it
    return_response = {'textract_job_started': responses}
    return {
        'statusCode': 200,
        'body': json.dumps(return_response)
    }


# functions    
def parse_args(event: Dict) -> Dict:
    '''
    Parse the environment variables and the event payload (from the lambda 
    entrypoint). Process them further if required.
    
    Usage
    -----
    args = parse_args(event)
    '''
    args = dict()

    first_record_body = json.loads(event['Records'][0]['body'])
    
    # get arguments from the event
    args['records'] = list()
    for record in first_record_body['Records']:
        args['records'].append({
            'bucket': record['s3']['bucket']['name'],
            'key': unquote_plus(record['s3']['object']['key']),
            'event_datetime': record['eventTime'],
            'event_user_id': record['userIdentity']['principalId'],
            'event_user_ip': record['requestParameters']['sourceIPAddress'],
        })
    
    # get the environement variables
    args['log_level'] = os.getenv('LOG_LEVEL', default='INFO')
    args['sns_topic_arn'] = os.getenv('SNS_TOPIC_ARN')
    args['sns_role_arn'] = os.getenv('SNS_ROLE_ARN')
    args['ddb_documents_table'] = os.getenv('DDB_DOCUMENTS_TABLE')
    args['textract_mode'] = os.getenv('TEXTRACT_MODE', default='TEXT').upper()
    args['textract_features'] = os.getenv('TEXTRACT_FEATURES', default='')

    # return
    return args


def textract_start_async_processing(
    input_bucket: str,
    input_key: str,
    sns_topic_arn: str,
    sns_role_arn: str,
    job_tag: str,
    mode: str,
    features: str,
) -> Dict:
    '''
    start an Amazon Textract asynchronous operation on a pdf document, which can be
    a single-page or multi-page document. Supports both TEXT detection and document ANALYSIS.
    Uses throttling to prevent quota violations.

    Usage
    -----
    job_id =  textract_start_async_processing(
        input_bucket, input_key,
        sns_topic_arn, sns_role_arn,
        job_tag, mode, features
    )
    '''
    # Use throttled client to respect rate limits
    textract_client, throttler = create_throttled_textract_client()

    textract_parameters = {
        'DocumentLocation': {
            'S3Object': {
                'Bucket': input_bucket,
                'Name': input_key
            }
        },
        'JobTag': job_tag,
        'NotificationChannel': {
            'SNSTopicArn': sns_topic_arn,
            'RoleArn': sns_role_arn
        }
    }

    # Parse and add features for ANALYSIS mode
    if mode == 'ANALYZE':
        feature_types = features.upper().replace(' ', '').split(',') if features else []
        # Validate features: only TABLES, FORMS, LAYOUT are valid
        valid_features = {'TABLES', 'FORMS', 'LAYOUT'}
        feature_types = [f for f in feature_types if f in valid_features]
        if feature_types:
            textract_parameters['FeatureTypes'] = feature_types
            logger.info(f"Using ANALYZE mode with features: {feature_types}")
        else:
            logger.warning("No valid features specified for ANALYSIS mode, using basic analysis")

        # Use throttled version for ANALYSIS
        response = throttler.throttled_start_document_analysis(
            textract_client, **textract_parameters
        )
    else:
        # Default to TEXT detection
        logger.info("Using TEXT detection mode")
        response = throttler.throttled_start_document_text_detection(
            textract_client, **textract_parameters
        )

    # return
    return response


def convert_datetime_s3_event(
    datetime_str: str, 
    output_datetime_format: str='%Y-%m-%dT%H:%M:%S+00:00'
) -> str:
    '''
    S3 events as received by the lambda function have the format `2021-04-15T16:20:02.994Z`.
    This function (and all functions convert_datetime*) converts it to 
    `2021-04-15T16:20:02.994+00:00`, the ISO 8601 format. The output format can be 
    modified via the output_datetime_format argument.
    '''
    dt = datetime.strptime(datetime_str[:-5], '%Y-%m-%dT%H:%M:%S')
    dt = dt + timedelta(milliseconds=int(datetime_str[-4:-1]))
    dt_str = dt.strftime(output_datetime_format)
    return dt_str


def convert_datetime_textract(
    datetime_str: str,
    output_datetime_format: str='%Y-%m-%dT%H:%M:%S+00:00'
) -> str:
    '''
    Convert the date of a textract async response (e.g. 'Mon, 19 Apr 2021 14:52:48 GMT') 
    to the ISO 8601 format (e.g. '2021-04-19T14:52:48+00:00'). The output format can be 
    modified via the output_datetime_format argument.
    '''
    dt = datetime.strptime(datetime_str, '%a, %d %b %Y %H:%M:%S %Z')
    dt_str = dt.strftime(output_datetime_format)
    return dt_str


def generate_uid(
    method: str='date', 
    datetime_format: str='%Y%m%dT%H%M%S-%f'
) -> str:
    '''
    Generate a random Unique ID (UID) base on the current datetime + a string of 
    8 random characters extracted from a UUID1 (default behavior).

    Usage
    -----
    random_uid = generate_uid(method='date')

    Arguments
    ---------
    type
        The method used to generate the random UID. either 'date' or 'uuid1'.

    Returns
    -------
    random_uid
        The random UID
    '''
    if method == 'date':
        now = datetime.now()
        now_as_str = now.strftime(datetime_format)
        random_chars = str(uuid.uuid1())[:8]
        return now_as_str + '-' + random_chars
    elif method == 'uuid1':
        return str(uuid.uuid1())
    else:
        raise AttributeError('Unknown "method". Valid options: [date|uuid1]')
