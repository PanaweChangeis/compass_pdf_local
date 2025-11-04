# PDF Preprocessing Engine

Converts scanned PDFs (non-selectable text) into searchable PDFs with selectable text using AWS Textract.


### Architecture

```
┌─────────────────┐
│  SelectablePdf  │
│     Lambda      │
│                 │
│ 1. Rasterize    │
│ 2. Add text     │
│ 3. Save         │
│    uncompressed │
│ 4. Upload to S3 │
│ 5. Trigger SF   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Step Functions  │
│  State Machine  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Fargate Task   │
│                 │
│ 1. Download PDF │
│ 2. Compress     │
│    (GhostScript)│
│ 3. Upload       │
│ 4. Update DDB   │
└─────────────────┘
```


## Overview

This application automatically processes PDF documents to make them searchable and text-selectable. It converts image-based text (like scanned documents) into real, selectable characters while preserving the original visual appearance.

## Use Cases

- **Document Search**: Enable search functionality in tools like Amazon Kendra
- **Document Indexing**: Prepare documents for Amazon CloudSearch
- **NLP Processing**: Extract text for natural language processing tasks
- **Accessibility**: Make scanned documents accessible to screen readers

## How It Works

1. **Upload** → PDF files to the input S3 bucket
2. **Process** → AWS Textract extracts text from images
3. **Generate** → Creates new PDF with transparent, selectable text overlay
4. **Download** → Retrieve processed PDFs from output S3 bucket

## Quick Start

### Deploy the Infrastructure

```bash
cd pdf-preprocessing/pdf_infra
cdk deploy PDFPreprocessStack -c env=<env> --profile <profile>
```

### Process PDFs

1. **Upload a PDF:**
   ```bash
   aws s3 cp your-document.pdf s3://<InputDocuments-bucket>/
   ```

2. **Wait for processing** (1-30 minutes depending on file size)

3. **Download the result:**
   ```bash
   aws s3 cp s3://<ProcessedDocuments-bucket>/your-document.pdf ./your-document-selectable.pdf
   ```

## What You Get

### Processed PDFs
- **Location**: `s3://pdfpreprocessstack-processeddocuments*/filename.pdf`
- **Features**: Original appearance with searchable, selectable text

### Textract JSON Data
- **Location**: `s3://pdfpreprocessstack-processeddocuments*/document_id/textract_output_blocks.json`
  - document_id can be found in DynamoDB table `pdfpreprocessstack-documents`.  Index `document_name_index2` can be used to find the instance of processing for a file if it's been run multiple times.  
- **Use**: Raw OCR data for NLP and advanced text processing
- **Documentation**: [Textract Response Objects](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-document-layout.html)

## Monitoring & Tracking

### Find Document Information
Query the DynamoDB table to link documents with their processing results:

```python
import boto3

# Find table name in CDK outputs or AWS Console
table_name = '<stack_name>-Documents<UID>'

ddb_client = boto3.client('dynamodb')
response = ddb_client.scan(TableName=table_name)

for item in response['Items']:
    doc_name = item['document_name']['S']
    doc_id = item['document_id']['S']
    print(f"Document: {doc_name} → ID: {doc_id}")
```

### Using Helper Tools
```python
from helpertools import ProcessingDdbTable

table_name = '<stack_name>-Documents<UID>'
doc_id = 'your_document_id'

ddb_table = ProcessingDdbTable(table_name)
items = ddb_table.get_items(doc_id)

for item in items:
    print(f"Document: {item['document_name']} → ID: {item['document_id']}")
```

### CloudWatch Logs
- **Location**: CloudWatch → Log Insights
- **Filter**: Select all log groups containing your stack name
- **Usage**: Comprehensive logging of all processing events

## Architecture Details

The system uses a serverless architecture with these components:

1. **Input S3 Bucket** → Triggers processing when PDFs are uploaded
2. **StartTextract Lambda** → Initiates asynchronous Textract jobs
3. **SNS Topic** → Handles Textract job status notifications
4. **ProcessTextract Lambda** → Downloads and stores Textract results
5. **SelectablePDF Lambda** → Creates final searchable PDFs
6. **DynamoDB Table** → Tracks processing status and document metadata
7. **Output S3 Bucket** → Stores processed documents and Textract data

## Processing Times

- **Small files** (< 10 MB): 1-5 minutes
- **Medium files** (10-50 MB): 5-15 minutes
- **Large files** (50-100+ MB): 15-30 minutes

## Handling Large Files

For very large PDFs (900+ pages or 1.8GB+ uncompressed), the system uses an asynchronous compression workflow to avoid Lambda timeouts:

### Problem
- Lambda functions have a 15-minute execution limit
- Large PDF compression (GhostScript) can take 9+ minutes
- Files like 1.8GB PDFs would timeout before completion

### Solution
- **SelectablePDF Lambda**: Creates searchable PDF and uploads uncompressed version to S3
- **Step Functions**: Orchestrates async compression workflow
- **Fargate Task**: Downloads, compresses (GhostScript), and uploads final PDF
- **Resources**: 4 vCPU, 8GB RAM, up to hours of processing time if needed

### Benefits
- **No Timeouts**: Fargate can run indefinitely for large files
- **Scalable**: Handles PDFs of any size
- **Cost-Effective**: Only pay for Fargate when processing large files
- **Reliable**: Maintains single document tracking in DynamoDB

### S3 Structure for Large Files
```
s3://processed-bucket/
└── {document-id}/
    ├── textract_output_blocks.json
    ├── uncompressed_{filename}.pdf    # Temporary (auto-deleted)
    └── {filename}.pdf                 # Final compressed PDF
```

### Monitoring Large File Processing
- **Step Functions Console**: Track compression workflow progress
- **ECS Logs**: `/ecs/compression/*` for compression task logs
- **DynamoDB**: Check `compression_complete` field for status and statistics

## Troubleshooting

### Common Issues
- **Long processing times**: Large files take longer; check CloudWatch logs
- **Failed processing**: Check DynamoDB table for error status
- **Missing outputs**: Verify S3 bucket permissions and processing completion

### Getting Help
- Check CloudWatch logs for detailed error information
- Query DynamoDB table for document processing status
- Verify input PDF is valid and not corrupted
