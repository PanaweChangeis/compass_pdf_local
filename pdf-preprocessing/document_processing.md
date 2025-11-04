# Document Processing Workflow

This document explains how PDF files are processed through the system, from upload to final output, and how to access the extracted data.

## Overview

The system processes PDF documents through AWS Textract to extract text, tables, forms, and layout information, then creates a searchable PDF with the extracted text overlaid on the original document.

## Processing Workflow

### 1. Upload Document

**Action:** Copy your PDF file to the input S3 bucket.

**Bucket Name:** The input bucket name is output when the CDK stack is deployed. Look for the CloudFormation output named `DocumentInputBucket`.

**Example:**
```bash
aws s3 cp your-document.pdf s3://[DocumentInputBucket]/your-document.pdf
```

### 2. Automatic Processing Pipeline

Once uploaded, the document goes through the following automated steps:

#### Step 1: Textract Job Initiation
- **Trigger:** S3 PUT event on the input bucket (for `.pdf` files)
- **Lambda:** `StartTextract`
- **Actions:**
  - Generates a unique document ID (format: `YYYYMMDDTHHMMSS-microseconds-UUID`)
  - Creates a DynamoDB record to track processing
  - Starts an asynchronous AWS Textract job
  - Textract mode: `ANALYZE` (extracts text, tables, forms, and layout)

#### Step 2: Textract Processing
- **Service:** AWS Textract (asynchronous)
- **Processing:** Analyzes the document and extracts:
  - Raw text content
  - Tables with cell structure
  - Form fields and key-value pairs
  - Layout and reading order
  - Confidence scores for each element

#### Step 3: Results Collection
- **Trigger:** SNS notification from Textract when job completes
- **Lambda:** `ProcessTextract`
- **Actions:**
  - Retrieves all Textract result blocks
  - Saves raw Textract JSON output to S3
  - Sends message to SQS queue for next processing step
  - Updates DynamoDB with completion status

#### Step 4: Searchable PDF Creation
- **Trigger:** SQS message from ProcessTextract
- **Lambda:** `SelectablePdf` (Docker-based)
- **Actions:**
  - Downloads original PDF and Textract results
  - Creates a searchable PDF by overlaying extracted text
  - Uploads final searchable PDF to processed bucket
  - Updates DynamoDB with final status

## Output Locations

### Processed Documents Bucket

**Bucket Name:** Look for the CloudFormation output named `DocumentOutputBucket`.

All outputs are organized by document ID in the following structure:

```
s3://[DocumentOutputBucket]/
└── [document-id]/
    ├── textract_output_blocks.json    # Raw Textract JSON results
    └── [original-filename].pdf         # Searchable PDF (final output)
```

### Finding Your Document's Outputs

#### Method 1: Using Document ID

If you know the document ID (from DynamoDB or logs):

```bash
# List all files for a specific document
aws s3 ls s3://[DocumentOutputBucket]/[document-id]/

# Download the searchable PDF
aws s3 cp s3://[DocumentOutputBucket]/[document-id]/[filename].pdf ./

# Download the raw Textract JSON
aws s3 cp s3://[DocumentOutputBucket]/[document-id]/textract_output_blocks.json ./
```

#### Method 2: Using DynamoDB

Query the DynamoDB table to find your document:

**Table Name:** Look for the CloudFormation output named `ProcessingLogsDynamoDB`.

```bash
# Query by document name
aws dynamodb query \
  --table-name [ProcessingLogsDynamoDB] \
  --index-name document_name_index2 \
  --key-condition-expression "document_name = :name" \
  --expression-attribute-values '{":name":{"S":"your-document.pdf"}}'
```

The response will include the `document_id` which you can use to locate files in S3.

#### Method 3: Browse S3 Console

1. Navigate to the S3 console
2. Open the `DocumentOutputBucket`
3. Browse folders by document ID (sorted by timestamp in the ID)
4. Each folder contains the outputs for one document

## Accessing Extracted Data

### Searchable PDF

**Location:** `s3://[DocumentOutputBucket]/[document-id]/[original-filename].pdf`

**Contents:**
- Original PDF with invisible text layer overlaid
- Fully searchable and copy-paste enabled
- Maintains original visual appearance

**Download:**
```bash
aws s3 cp s3://[DocumentOutputBucket]/[document-id]/[filename].pdf ./searchable-document.pdf
```

### Raw Textract JSON

**Location:** `s3://[DocumentOutputBucket]/[document-id]/textract_output_blocks.json`

**Contents:** Complete Textract analysis results including:
- **Blocks:** All detected elements (PAGE, LINE, WORD, TABLE, CELL, etc.)
- **Text:** Extracted text with confidence scores
- **Tables:** Structured table data with row/column information
- **Forms:** Key-value pairs from form fields
- **Layout:** Reading order and spatial relationships
- **Geometry:** Bounding box coordinates for each element

**Download:**
```bash
aws s3 cp s3://[DocumentOutputBucket]/[document-id]/textract_output_blocks.json ./textract-results.json
```

### Textract JSON Structure

The `textract_output_blocks.json` file contains:

```json
{
  "Blocks": [
    {
      "BlockType": "PAGE|LINE|WORD|TABLE|CELL|KEY_VALUE_SET|...",
      "Confidence": 99.5,
      "Text": "extracted text",
      "Geometry": {
        "BoundingBox": {
          "Width": 0.5,
          "Height": 0.1,
          "Left": 0.1,
          "Top": 0.2
        }
      },
      "Relationships": [...],
      "Id": "block-id",
      ...
    }
  ],
  "DocumentMetadata": {
    "Pages": 5
  }
}
```

**Key Block Types:**
- `PAGE`: Represents a page in the document
- `LINE`: A line of text
- `WORD`: Individual words with confidence scores
- `TABLE`: Table structure
- `CELL`: Individual table cells
- `KEY_VALUE_SET`: Form fields (key-value pairs)
- `LAYOUT_*`: Layout elements (headers, footers, sections, etc.)

## Finding the Most Recent Run

### Using AWS CLI

To find the most recent processing run for a specific document:

```bash
# List all versions sorted by timestamp (document IDs contain timestamps)
aws s3 ls s3://[DocumentOutputBucket]/ | sort -r

# The most recent document ID will be at the top
# Document IDs format: YYYYMMDDTHHMMSS-microseconds-UUID
```

### Using DynamoDB

Query for the most recent processing of a specific document name:

```bash
aws dynamodb query \
  --table-name [ProcessingLogsDynamoDB] \
  --index-name document_name_index2 \
  --key-condition-expression "document_name = :name" \
  --expression-attribute-values '{":name":{"S":"your-document.pdf"}}' \
  --scan-index-forward false \
  --limit 1
```

This returns the most recent document ID for that filename.

## Processing Status Tracking

### DynamoDB Record Structure

Each document has a DynamoDB record with the following information:

```json
{
  "document_id": "20250117T103045-123456-abc123",
  "document_name": "your-document.pdf",
  "document_s3": {
    "bucket": "input-bucket",
    "key": "your-document.pdf"
  },
  "document_put_event": {
    "datetime": "2025-01-17T10:30:45+00:00",
    "user_id": "...",
    "user_ip": "..."
  },
  "textract_async_start": {
    "job_id": "textract-job-id",
    "datetime": "2025-01-17T10:30:46+00:00"
  },
  "textract_async_end": {
    "datetime": "2025-01-17T10:32:15+00:00"
  }
}
```

### Check Processing Status

```bash
# Get document details by ID
aws dynamodb get-item \
  --table-name [ProcessingLogsDynamoDB] \
  --key '{"document_id":{"S":"[document-id]"},"document_name":{"S":"your-document.pdf"}}'
```

## Common Use Cases

### Extract All Text from a Document

1. Download the Textract JSON:
   ```bash
   aws s3 cp s3://[DocumentOutputBucket]/[document-id]/textract_output_blocks.json ./
   ```

2. Parse the JSON to extract text:
   ```python
   import json
   
   with open('textract_output_blocks.json', 'r') as f:
       data = json.load(f)
   
   # Extract all text in reading order
   text_blocks = [block for block in data['Blocks'] if block['BlockType'] == 'LINE']
   full_text = '\n'.join([block['Text'] for block in text_blocks])
   ```

### Extract Tables

```python
import json

with open('textract_output_blocks.json', 'r') as f:
    data = json.load(f)

# Find all tables
tables = [block for block in data['Blocks'] if block['BlockType'] == 'TABLE']

# Extract table cells
for table in tables:
    # Process table structure using relationships
    # See AWS Textract documentation for detailed table parsing
    pass
```

### Get Document with Highest Confidence

The Textract results include confidence scores for each detected element. You can use these to assess quality:

```python
import json

with open('textract_output_blocks.json', 'r') as f:
    data = json.load(f)

# Calculate average confidence
confidences = [block['Confidence'] for block in data['Blocks'] if 'Confidence' in block]
avg_confidence = sum(confidences) / len(confidences)
print(f"Average confidence: {avg_confidence:.2f}%")
```

## Troubleshooting

### Document Not Processing

1. **Check S3 upload:** Verify the file was uploaded to the correct input bucket
2. **Check file extension:** Only `.pdf` files trigger processing
3. **Check DynamoDB:** Look for the document record to see if processing started
4. **Check CloudWatch Logs:** Review Lambda function logs for errors

### Finding Failed Jobs

Query DynamoDB for documents without completion timestamps:

```bash
aws dynamodb scan \
  --table-name [ProcessingLogsDynamoDB] \
  --filter-expression "attribute_not_exists(textract_async_end)"
```

### Accessing CloudWatch Logs

Lambda function logs are available in CloudWatch:
- `/aws/lambda/[StackName]-StartTextract-*`
- `/aws/lambda/[StackName]-ProcessTextract-*`
- `/aws/lambda/[StackName]-SelectablePdf-*`

## Configuration

### Textract Mode

The system is configured to use `ANALYZE` mode by default, which extracts:
- Text (with reading order)
- Tables (with structure)
- Forms (key-value pairs)
- Layout (headers, footers, sections)

This is configured in the CDK stack via environment variables:
- `TEXTRACT_MODE`: `ANALYZE`
- `TEXTRACT_FEATURES`: `TABLES,FORMS,LAYOUT`

### Processing Limits

The system includes throttling to respect AWS Textract quotas:
- Maximum concurrent jobs: 90 (out of 100 limit)
- Start job TPS limit: 1.8 requests/second
- Get results TPS limit: 4.5 requests/second

## Summary

**To process a document:**
1. Upload PDF to input bucket
2. Wait for automatic processing (typically 1-5 minutes depending on document size)
3. Find outputs in processed bucket under `[document-id]/`

**To access results:**
- **Searchable PDF:** `s3://[DocumentOutputBucket]/[document-id]/[filename].pdf`
- **Raw Textract JSON:** `s3://[DocumentOutputBucket]/[document-id]/textract_output_blocks.json`
- **Processing metadata:** Query DynamoDB table using document name or ID
