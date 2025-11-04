# load modules
# ------------
import shutil
from constructs import Construct
from aws_cdk import (
    Stack,
    aws_iam,
    aws_s3,
    aws_s3_notifications as s3n,
    aws_lambda,
    aws_lambda_event_sources,
    aws_sns,
    aws_sqs,
    aws_sns_subscriptions,
    aws_dynamodb,
    aws_ecr_assets,
    aws_ecs,
    aws_ec2,
    aws_stepfunctions,
    aws_logs,
    RemovalPolicy,
    Duration,
    Size,
    CfnOutput,
)
try:
    from aws_cdk import aws_lambda_python_alpha as aws_lambda_python
except ImportError:
    # Fallback to manual layer creation if aws_lambda_python_alpha is not available
    aws_lambda_python = None
import subprocess
import nltk
import sys
import os
import pathlib
from typing import Dict, List, Optional

# Module environment variables
# ----------------------------
CURRENT_FILEPATH = pathlib.Path(__file__).absolute()
CURRENT_DIRPATH = CURRENT_FILEPATH.parent.absolute()
LAMBDA_DIRPATH = CURRENT_DIRPATH.joinpath('lambda')
LIB_DIRPATH = CURRENT_DIRPATH.joinpath('lambda_layer')


# classes
# -------
class PdfPreprocessStack(Stack):

    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        env_config: Dict,
        log_level: str='INFO',
        add_final_sns: bool=False,
        **kwargs) -> None:
        '''
        '''
        super().__init__(scope, construct_id, **kwargs)

        self.env_config = env_config
        # bucket for the original PDF. They might not be searchable, i.e. they 
        # are not made of characters, just images (e.g. scanned documents)
        self.doc_bucket = aws_s3.Bucket(
            self,
            id='InputDocuments',
            removal_policy=RemovalPolicy.DESTROY, #kept if not empty
        )
        doc_bucket_r_policy = aws_iam.Policy(self, 'DocBucketRead',
            statements=[aws_iam.PolicyStatement(actions=['s3:GetObject'],
            resources=[self.doc_bucket.bucket_arn+'/*'])]
        )

        self.processed_bucket = aws_s3.Bucket(
            self,
            id='ProcessedDocuments',
            removal_policy=RemovalPolicy.DESTROY, #kept if not empty
        )
        processed_bucket_rw_policy = aws_iam.Policy(self, 'ProcessedBucketReadWrite',
            statements=[aws_iam.PolicyStatement(actions=['s3:GetObject','s3:PutObject', 's3:HeadObject', 's3:ListBucket'],
            resources=[self.processed_bucket.bucket_arn+'/*', self.processed_bucket.bucket_arn])]
        )

        # Output bucket for final compressed PDFs (latest version only, with versioning enabled)
        self.output_bucket = aws_s3.Bucket(
            self,
            id='OutputDocuments',
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,  # Enable versioning to preserve history
            lifecycle_rules=[
                aws_s3.LifecycleRule(
                    noncurrent_version_expiration=Duration.days(90)  # Keep old versions for 90 days
                )
            ]
        )
        output_bucket_rw_policy = aws_iam.Policy(self, 'OutputBucketReadWrite',
            statements=[aws_iam.PolicyStatement(actions=['s3:GetObject','s3:PutObject', 's3:HeadObject', 's3:ListBucket'],
            resources=[self.output_bucket.bucket_arn+'/*', self.output_bucket.bucket_arn])]
        )

        # create the DynamoDB tables. We create N tables:
        # 1. A table to store the info about documents processing
        self.ddb_documents_table = aws_dynamodb.Table(
            self,
            id='Documents',
            partition_key=aws_dynamodb.Attribute(
                name='document_id', type=aws_dynamodb.AttributeType.STRING
            ),
            sort_key=aws_dynamodb.Attribute(
                name='document_name', type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST
        )
        index_arn = f"{self.ddb_documents_table.table_arn}/index/document_name_index2"
        ddb_documents_table_policy = aws_iam.Policy(self,f'DdbDocTablePolicy',
            statements=[aws_iam.PolicyStatement(
                actions=['dynamodb:PutItem','dynamodb:UpdateItem','dynamodb:UpdateTable', 'dynamodb:DeleteItem', 'dynamodb:DescribeTable', 'dynamodb:GetItem', 'dynamodb:BatchGetItem'],
                resources=[self.ddb_documents_table.table_arn]
            ), aws_iam.PolicyStatement(
                actions=['dynamodb:Query'],
                resources=[self.ddb_documents_table.table_arn, index_arn]
            )]
        )

        # Add a global secondary index on the "document_name" field
        self.ddb_documents_table.add_global_secondary_index(
            partition_key=aws_dynamodb.Attribute(
                name='document_name', type=aws_dynamodb.AttributeType.STRING
            ),
            sort_key=aws_dynamodb.Attribute(
                name='document_id', type=aws_dynamodb.AttributeType.STRING
            ),
            index_name='document_name_index2'
        )

        # SNS topic for Textract and role to use it. The role is used by textract to publish 
        # its status (i.e. success or fail). Textract processing can be long, especially in a 
        # busy queue! Therefore we set the assume role timeout to 6 hours
        textract_job_topic = aws_sns.Topic(self, id='textract-job-status')
        assume_role_timeout = 6 * 3600
        sns_publish_role = aws_iam.Role(
            self,
            id='SnsPublishRole',
            assumed_by=aws_iam.ServicePrincipal('textract.amazonaws.com'),
            max_session_duration=Duration.seconds(assume_role_timeout),
        )
        sns_publish_role.add_to_policy(
            statement=aws_iam.PolicyStatement(
                sid='SnsPublishRight',
                effect=aws_iam.Effect.ALLOW,
                resources=[textract_job_topic.topic_arn],
                actions=['sns:Publish'],
            )
        )

        # Create the lambda layers using modern CDK constructs
        # Migrated from aws-cdk-lambda-layer-builder to native CDK with improved bundling
        if aws_lambda_python:
            # Use PythonLayerVersion if available (CDK v2.50+)
            textracttools_layer = aws_lambda_python.PythonLayerVersion(
                self,
                id='TextractTools',
                entry=str(LIB_DIRPATH.joinpath('textracttools')),
                compatible_runtimes=[aws_lambda.Runtime.PYTHON_3_12],
                description='TextractTools python module'
            )

            helpertools_layer = aws_lambda_python.PythonLayerVersion(
                self,
                id='HelperTools',
                entry=str(LIB_DIRPATH.joinpath('helpertools')),
                compatible_runtimes=[aws_lambda.Runtime.PYTHON_3_12],
                description='HelperTools python module'
            )

            # X-Ray SDK layer for tracing
            xray_layer = aws_lambda_python.PythonLayerVersion(
                self,
                id='XRaySDK',
                entry=str(LIB_DIRPATH.joinpath('xraysdk')),
                compatible_runtimes=[aws_lambda.Runtime.PYTHON_3_12],
                description='AWS X-Ray SDK for Python'
            )
        else:
            # Fallback to improved LayerVersion with better bundling
            textracttools_layer = aws_lambda.LayerVersion(
                self,
                id='TextractTools',
                code=aws_lambda.Code.from_asset(
                    path=str(LIB_DIRPATH.joinpath('textracttools')),
                    bundling={
                        "image": aws_lambda.Runtime.PYTHON_3_12.bundling_image,
                        "command": [
                            'bash', '-c', 
                            'mkdir -p /asset-output/python && '
                            'cp -au . /asset-output/python && '
                            'cd /asset-output/python && '
                            'pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt -t /asset-output/python && '
                            'find /asset-output/python -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true'
                        ],
                        "network": "host"
                    }
                ),
                compatible_runtimes=[aws_lambda.Runtime.PYTHON_3_12],
                description='TextractTools python module - migrated from aws-cdk-lambda-layer-builder'
            )

            helpertools_layer = aws_lambda.LayerVersion(
                self,
                id='HelperTools',
                code=aws_lambda.Code.from_asset(
                    path=str(LIB_DIRPATH.joinpath('helpertools')),
                    bundling={
                        "image": aws_lambda.Runtime.PYTHON_3_12.bundling_image,
                        "command": [
                            'bash', '-c', 
                            'mkdir -p /asset-output/python && '
                            'cp -au . /asset-output/python && '
                            'cd /asset-output/python && '
                            'pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt -t /asset-output/python && '
                            'find /asset-output/python -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true'
                        ],
                        "network": "host"
                    }
                ),
                compatible_runtimes=[aws_lambda.Runtime.PYTHON_3_12],
                description='HelperTools python module - migrated from aws-cdk-lambda-layer-builder'
            )

            # X-Ray SDK layer for tracing
            xray_layer = aws_lambda.LayerVersion(
                self,
                id='XRaySDK',
                code=aws_lambda.Code.from_asset(
                    path=str(LIB_DIRPATH.joinpath('xraysdk')),
                    bundling={
                        "image": aws_lambda.Runtime.PYTHON_3_12.bundling_image,
                        "command": [
                            'bash', '-c', 
                            'mkdir -p /asset-output/python && '
                            'cp -au . /asset-output/python && '
                            'cd /asset-output/python && '
                            'pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt -t /asset-output/python && '
                            'find /asset-output/python -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true'
                        ],
                        "network": "host"
                    }
                ),
                compatible_runtimes=[aws_lambda.Runtime.PYTHON_3_12],
                description='AWS X-Ray SDK for Python - tracing support'
            )
        # lambda function starting textract. The lambda is triggered with a S3 PUT 
        # notification
        lambda_timeout_sec = 1 * 60  #1 minute 

        self.start_textract_lambda = aws_lambda.Function(
            self,
            id='StartTextract',
            runtime=aws_lambda.Runtime.PYTHON_3_12,  # Updated to Python 3.12
            handler='main.lambda_handler',
            code=aws_lambda.Code.from_asset(os.path.join(LAMBDA_DIRPATH, 'start_textract')),
            timeout=Duration.seconds(lambda_timeout_sec),
            layers=[helpertools_layer, xray_layer],
            environment={
                'LOG_LEVEL': log_level,
                'SNS_TOPIC_ARN': textract_job_topic.topic_arn,
                'SNS_ROLE_ARN': sns_publish_role.role_arn,
                'DDB_DOCUMENTS_TABLE': self.ddb_documents_table.table_name,
                # Textract mode and features configuration
                'TEXTRACT_MODE': 'ANALYZE',  # 'TEXT' or 'ANALYZE' - set to 'ANALYZE' for table/form extraction
                'TEXTRACT_FEATURES': 'TABLES,FORMS,LAYOUT',  # Comma-delimited: TABLES,FORMS,LAYOUT - only used if mode is ANALYZE
                # Textract quota management environment variables
                'TEXTRACT_START_TPS_LIMIT': '1.8',  # More aggressive since single-threaded
                'TEXTRACT_GET_TPS_LIMIT': '4.5',    # More aggressive since single-threaded
                'TEXTRACT_MAX_CONCURRENT_JOBS': '90',  # Conservative: 90% of 100 limit
                'TEXTRACT_MAX_RETRIES': '5',
                'TEXTRACT_BASE_DELAY': '1.0',
                'TEXTRACT_MAX_DELAY': '60.0'
            },
            retry_attempts=0,
            memory_size=128,  #128MB
            reserved_concurrent_executions=1,  # CRITICAL: Single-threaded processing for quota management
            tracing=aws_lambda.Tracing.ACTIVE  # Enable X-Ray tracing
        )

        sqs_visibility_timeout_sec = 15 * 60
        lambda_timeout_sec = sqs_visibility_timeout_sec-1
        # add the required policies to the default role creation with the lambda 
        # start_textract_lambda
        # Create CloudWatch policy for concurrent job tracking
        cloudwatch_policy = aws_iam.Policy(self, 'CloudWatchMetricsRead',
            statements=[aws_iam.PolicyStatement(
                actions=['cloudwatch:GetMetricStatistics'],
                resources=['*']
            )]
        )

        self.start_textract_lambda.role.add_managed_policy(
                aws_iam.ManagedPolicy.from_aws_managed_policy_name('AmazonTextractFullAccess')
        )
        self.start_textract_lambda.role.attach_inline_policy(doc_bucket_r_policy)
        self.start_textract_lambda.role.attach_inline_policy(ddb_documents_table_policy)
        self.start_textract_lambda.role.attach_inline_policy(cloudwatch_policy)

        # Create the SQS queue that will handle all PDF PUTS from the S3 bucket
        s3_event_queue_dlq = aws_sqs.Queue(
            self, 
            id='S3EventQueueDlq'
        )
        s3_event_queue = aws_sqs.Queue(
            self, 
            id='S3EventQueue',
            visibility_timeout=Duration.seconds(sqs_visibility_timeout_sec),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=1, 
                queue=s3_event_queue_dlq
            )
        )

        self.doc_bucket.add_event_notification(
            aws_s3.EventType.OBJECT_CREATED,
            s3n.SqsDestination(s3_event_queue),
            aws_s3.NotificationKeyFilter(suffix='.pdf')
        )

        # set the trigger: S3 PUT on doc_bucket
        self.start_textract_lambda.add_event_source(
            source=aws_lambda_event_sources.SqsEventSource(
                queue=s3_event_queue,
                batch_size=1  # adjust as needed
            )
        )

        # Lambda getting the textract results from S3 and feeding textract output 
        # status to a SQS for future faning
        dlq_processed_textracted_queue_sqs = aws_sqs.Queue(self, id='ProcessedTextractQueueDlq')
        processed_textracted_queue_sqs = aws_sqs.Queue(
            self,
            id='ProcessedTextractQueue',
            visibility_timeout=Duration.seconds(sqs_visibility_timeout_sec),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=1, 
                queue=dlq_processed_textracted_queue_sqs
            )
        )
        textract_output_sqs_w_policy = aws_iam.Policy(self, 'SqsPublishMessage',
            statements=[aws_iam.PolicyStatement(actions=['sqs:SendMessage'],
                resources=[processed_textracted_queue_sqs.queue_arn])]
        )

        self.process_textract_lambda = aws_lambda.Function(
            self,
            id='ProcessTextract',
            runtime=aws_lambda.Runtime.PYTHON_3_12,  # Updated to Python 3.12
            handler='main.lambda_handler',
            code=aws_lambda.Code.from_asset(os.path.join(LAMBDA_DIRPATH, 'process_textract')),
            layers=[textracttools_layer, helpertools_layer, xray_layer],
            timeout=Duration.seconds(lambda_timeout_sec),
            reserved_concurrent_executions=100,
            environment={
                'LOG_LEVEL': log_level,
                'REGION': self.region,
                'DDB_DOCUMENTS_TABLE': self.ddb_documents_table.table_name,
                'TEXTRACT_BUCKET': self.processed_bucket.bucket_name,
                'TEXTRACT_RES_QUEUE_URL': processed_textracted_queue_sqs.queue_url,
                # Textract mode configuration (must match start_textract mode)
                'TEXTRACT_MODE': 'ANALYZE',  # 'TEXT' or 'ANALYZE' - synchronized with start_textract
                # Textract quota management environment variables
                'TEXTRACT_GET_TPS_LIMIT': '4.5',    # For GetDocumentTextDetection/Analysis calls
                'TEXTRACT_MAX_RETRIES': '5',
                'TEXTRACT_BASE_DELAY': '1.0',
                'TEXTRACT_MAX_DELAY': '60.0'
            },
            retry_attempts=0, 
            memory_size=2048,
            tracing=aws_lambda.Tracing.ACTIVE  # Enable X-Ray tracing
        )
        # add the required policies to the default role create with the lambda
        self.process_textract_lambda.role.add_managed_policy(
                aws_iam.ManagedPolicy.from_aws_managed_policy_name('AmazonTextractFullAccess')
            )
        self.process_textract_lambda.role.attach_inline_policy(ddb_documents_table_policy)
        self.process_textract_lambda.role.attach_inline_policy(doc_bucket_r_policy)
        self.process_textract_lambda.role.attach_inline_policy(processed_bucket_rw_policy)
        self.process_textract_lambda.role.attach_inline_policy(textract_output_sqs_w_policy)
        # set the trigger
        textract_job_topic.add_subscription(
            aws_sns_subscriptions.LambdaSubscription(self.process_textract_lambda)
        )
       

        # Lambda function turning a scanned PDF (i.e. a PDF where we cannot select 
        # text) into a searchable PDF (i.e. a PDF where we can select text). This function 
        # received message from sqs, therefore its timeout MUST be smaller than the 
        # visibility timeout of the source SQS, otherwise, cyclic call!!.
        
        self.selectable_pdf_lambda = aws_lambda.DockerImageFunction(
            self,
            id='SelectablePdf',
            code=aws_lambda.DockerImageCode.from_image_asset(
                directory=str(CURRENT_DIRPATH),  # Build context includes lambda_layer/
                file="docker_files/selectable_pdf/Dockerfile",  # Specify Dockerfile path
                exclude=[
                    "cdk.out/**",
                    "**/__pycache__/**",
                    "**/*.pyc",
                    ".git/**",
                    # Exclude other lambda functions that don't affect this image
                    "lambda/start_textract/**",
                    "lambda/process_textract/**"
                ],
                build_args={
                    "BUILDKIT_INLINE_CACHE": "1"  # Enable build cache for faster rebuilds
                },
                network_mode=aws_ecr_assets.NetworkMode.HOST,
                platform=aws_ecr_assets.Platform.LINUX_AMD64  # Explicitly specify x86_64 architecture for Lambda
            ),
            timeout=Duration.seconds(lambda_timeout_sec),
            environment={
                'DDB_DOCUMENTS_TABLE': self.ddb_documents_table.table_name,
                'OUTPUT_BUCKET': self.processed_bucket.bucket_name,
                'LOG_LEVEL': 'INFO',
                'ADD_WORD_BBOX': '0',
                'SHOW_CHARACTER': '0',
                'PDF_IMAGE_DPI': '120',  # Reduced from 200 to 120 for better size/quality balance
                'PDF_COLOR_SPACE': 'GRAY',  # Use grayscale instead of RGB for smaller file sizes
                'FORCE_RASTERIZATION': 'false',  # Enable text replacement strategy
            },
            retry_attempts=0,
            memory_size=8192,
            ephemeral_storage_size=Size.gibibytes(4),
            tracing=aws_lambda.Tracing.ACTIVE  # Enable X-Ray tracing
        )
        # add the required policies to the default role creation with the lambda
        self.selectable_pdf_lambda.role.attach_inline_policy(ddb_documents_table_policy)
        self.selectable_pdf_lambda.role.attach_inline_policy(doc_bucket_r_policy)
        self.selectable_pdf_lambda.role.attach_inline_policy(processed_bucket_rw_policy)
        # add the SQS trigger
        self.selectable_pdf_lambda.add_event_source(
            source=aws_lambda_event_sources.SqsEventSource(
                queue=processed_textracted_queue_sqs,
                batch_size=1,
            )
        )

        # add final SNS topic if required by user
        if add_final_sns:
            self.final_sns_topic = aws_sns.Topic(self, 'FinalTopic')
            self.selectable_pdf_lambda.add_environment('FINAL_SNS_TOPIC_ARN',self.final_sns_topic.topic_arn)
            self.selectable_pdf_lambda.role.attach_inline_policy(
                 aws_iam.Policy(self, 'FInalTopicWPolicy',
                    statements=[aws_iam.PolicyStatement(actions=['sns:Publish'],
                        resources=[self.final_sns_topic.topic_arn])]
                )
            )

        # ECS Cluster and Fargate Task for async PDF compression
        # -------------------------------------------------------
        
        # Get VPC ID from context (passed via -c env=test)
        # If vpc_id is provided in context, use it; otherwise fall back to default VPC
        vpc_id = env_config.get('vpc_id')
        if vpc_id:
            vpc = aws_ec2.Vpc.from_lookup(self, 'FargateVPC', vpc_id=vpc_id)
        else:
            # Fallback to default VPC if no vpc_id in context
            vpc = aws_ec2.Vpc.from_lookup(self, 'FargateVPC', is_default=True)
        
        # Create ECS Cluster
        ecs_cluster = aws_ecs.Cluster(
            self,
            id='CompressionCluster',
            vpc=vpc,
            container_insights=True
        )
        
        # Create Fargate Task Definition for PDF compression
        compression_task_def = aws_ecs.FargateTaskDefinition(
            self,
            id='CompressionTaskDef',
            memory_limit_mib=8192,  # 8GB memory
            cpu=4096,  # 4 vCPU
            ephemeral_storage_gib=50  # 50GB ephemeral storage for large PDFs
        )
        
        # Build and add container from Dockerfile
        compression_container = compression_task_def.add_container(
            id='compress-pdf',
            image=aws_ecs.ContainerImage.from_asset(
                directory=str(CURRENT_DIRPATH),
                file="docker_files/compress_pdf/Dockerfile",
                exclude=[
                    "cdk.out/**",
                    "**/__pycache__/**",
                    "**/*.pyc",
                    ".git/**"
                ],
                platform=aws_ecr_assets.Platform.LINUX_AMD64
            ),
            logging=aws_ecs.LogDrivers.aws_logs(
                stream_prefix='compression',
                log_retention=aws_logs.RetentionDays.ONE_WEEK
            )
        )
        
        # Grant permissions to Fargate task
        self.processed_bucket.grant_read_write(compression_task_def.task_role)
        self.output_bucket.grant_read_write(compression_task_def.task_role)
        self.ddb_documents_table.grant_read_write_data(compression_task_def.task_role)
        
        # Create Step Functions State Machine for compression workflow
        # Read the state machine definition from JSON file
        import json as json_lib
        with open(str(CURRENT_DIRPATH.joinpath('step_functions/compress_pdf_workflow.json')), 'r') as f:
            state_machine_def_template = f.read()
        
        # Replace placeholders with actual ARNs (subnets will be passed at runtime)
        state_machine_def = state_machine_def_template.replace(
            '${Partition}', self.partition
        ).replace(
            '${ECSClusterArn}', ecs_cluster.cluster_arn
        ).replace(
            '${TaskDefinitionArn}', compression_task_def.task_definition_arn
        )
        
        # Store subnet IDs as environment variable for Lambda to use
        # When using from_lookup, we need to select subnets explicitly
        # Using PRIVATE_WITH_EGRESS for private subnets with NAT/Transit Gateway
        selected_subnets = vpc.select_subnets(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS)
        subnet_ids = selected_subnets.subnet_ids
        
        # Create Step Functions State Machine with X-Ray tracing enabled
        compression_state_machine = aws_stepfunctions.CfnStateMachine(
            self,
            id='CompressionStateMachine',
            role_arn=self._create_step_functions_role(ecs_cluster, compression_task_def).role_arn,
            definition_string=state_machine_def,
            state_machine_name=f'{construct_id}-PDFCompression',
            tracing_configuration=aws_stepfunctions.CfnStateMachine.TracingConfigurationProperty(
                enabled=True
            )
        )
        
        # Add Step Functions ARN, subnet IDs, and output bucket to SelectablePdf Lambda environment
        self.selectable_pdf_lambda.add_environment(
            'COMPRESSION_STATE_MACHINE_ARN',
            compression_state_machine.attr_arn
        )
        self.selectable_pdf_lambda.add_environment(
            'FARGATE_SUBNETS',
            ','.join(subnet_ids)
        )
        self.selectable_pdf_lambda.add_environment(
            'OUTPUT_FINAL_BUCKET',
            self.output_bucket.bucket_name
        )
        
        # Grant SelectablePdf Lambda permission to start Step Functions executions
        self.selectable_pdf_lambda.role.attach_inline_policy(
            aws_iam.Policy(self, 'StartStepFunctionsPolicy',
                statements=[aws_iam.PolicyStatement(
                    actions=['states:StartExecution'],
                    resources=[compression_state_machine.attr_arn]
                )]
            )
        )

        # stack output
        # buckets
        output_prefix = construct_id
        CfnOutput(
            self,
            id='DocumentInputBucket',
            value=self.doc_bucket.bucket_name,
            description='Bucket where to load the PDFs',
            export_name=f'{output_prefix}DocumentInputBucket',
        )
        CfnOutput(
            self,
            id='DocumentProcessedBucket',
            value=self.processed_bucket.bucket_name,
            description='Bucket where the processed PDFs and the intermediary files are stored (with document_id folders)',
            export_name=f'{output_prefix}DocumentProcessedBucket',
        )
        CfnOutput(
            self,
            id='DocumentOutputBucket',
            value=self.output_bucket.bucket_name,
            description='Bucket where the final compressed PDFs are stored (latest version only, with versioning)',
            export_name=f'{output_prefix}DocumentOutputBucket',
        )
        
        # dynamodb tables
        CfnOutput(
            self,
            id='ProcessingLogsDynamoDB',
            value=self.ddb_documents_table.table_name,
            description='Documents',
            export_name=f'{output_prefix}Documents',
        )
    
    def _create_step_functions_role(self, ecs_cluster, task_definition):
        """Create IAM role for Step Functions to run ECS tasks"""
        role = aws_iam.Role(
            self,
            id='StepFunctionsECSRole',
            assumed_by=aws_iam.ServicePrincipal('states.amazonaws.com'),
            description='Role for Step Functions to run ECS Fargate tasks'
        )
        
        # Grant permissions to run ECS tasks
        role.add_to_policy(
            aws_iam.PolicyStatement(
                actions=[
                    'ecs:RunTask',
                    'ecs:StopTask',
                    'ecs:DescribeTasks'
                ],
                resources=[task_definition.task_definition_arn]
            )
        )
        
        # Grant permissions to pass IAM roles to ECS
        role.add_to_policy(
            aws_iam.PolicyStatement(
                actions=['iam:PassRole'],
                resources=[
                    task_definition.task_role.role_arn,
                    task_definition.execution_role.role_arn
                ]
            )
        )
        
        # Grant permissions for ECS events
        role.add_to_policy(
            aws_iam.PolicyStatement(
                actions=[
                    'events:PutTargets',
                    'events:PutRule',
                    'events:DescribeRule'
                ],
                resources=['*']
            )
        )
        
        return role
