# PDF Preprocessing Infrastructure

This AWS CDK stack converts scanned PDFs (non-selectable text) into searchable PDFs with selectable text using AWS Textract.

## Overview

The system automatically processes PDF documents uploaded to an S3 bucket, extracts text using AWS Textract, and generates new PDFs with selectable text layers.

## Prerequisites

Before deploying, ensure you have:

- **AWS Account** with appropriate permissions
- **Python 3.12+** installed
- **AWS CLI** configured (`aws configure`)
- **AWS CDK v2.x** installed (`npm install -g aws-cdk`)

### Installation Links
- [AWS CLI Setup Guide](https://aws.amazon.com/cli/)
- [CDK Getting Started](https://docs.aws.amazon.com/cdk/latest/guide/getting_started.html)

### Bootstrap CDK (One-time setup)
```bash
cdk bootstrap
```

## Quick Start

1. **Set up Python environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install --upgrade pip
   pip install .
   ```

2. **Deploy the stack:**
   ```bash
   cdk deploy
   ```

3. **Use the system:**
   - Upload PDFs to the input bucket (shown in deployment output)
   - Processed PDFs will appear in the output bucket

## Deployment Output

After successful deployment, you'll see:
```
✅  PdfPreprocessStack

Outputs:
PdfPreprocessStack.DocumentInputBucket = pdf-preprocess-input-xxxxx
PdfPreprocessStack.DocumentOutputBucket = pdf-preprocess-output-xxxxx
PdfPreprocessStack.ProcessingLogsDynamoDB = pdf-preprocess-logs-xxxxx
```

## Configuration

To customize the stack name or description:
1. Edit `app.py`
2. Modify the stack parameters
3. Run `cdk deploy` again

## Useful Commands

| Command | Description |
|---------|-------------|
| `cdk ls` | List all stacks |
| `cdk synth` | Generate CloudFormation template |
| `cdk deploy` | Deploy to AWS |
| `cdk diff` | Show changes since last deployment |
| `cdk destroy` | Remove the stack from AWS |
| `cdk docs` | Open CDK documentation |

## Troubleshooting

**Installation Issues:**
- Ensure Python 3.12+ is installed
- Use `pip install . --force-reinstall` if dependencies aren't updating

**Deployment Issues:**
- Verify AWS credentials: `aws sts get-caller-identity`
- Ensure CDK is bootstrapped: `cdk bootstrap`
- Check AWS permissions for CDK deployment

## Textract Quota Management

This system includes built-in quota management to prevent exceeding AWS Textract service limits during bulk document processing.

### Key Features
- **Rate Limiting**: Automatically throttles API calls to stay within TPS limits
- **Concurrent Job Management**: Tracks and limits concurrent Textract jobs
- **Exponential Backoff**: Handles throttling errors with intelligent retry logic
- **Configurable Limits**: Environment variables allow customization for different quotas

### Default Quotas Protected
- StartDocumentTextDetection: 2 TPS → Limited to 1.5 TPS (safe margin)
- GetDocumentTextDetection: 5 TPS → Limited to 4.0 TPS (safe margin)
- Concurrent Jobs: 100 → Limited to 90 jobs (safe margin)

### Configuration
The quota limits can be customized via environment variables. See [TEXTRACT_QUOTA_CONFIGURATION.md](./TEXTRACT_QUOTA_CONFIGURATION.md) for detailed configuration options.

For immediate deployment, no configuration is needed - the system uses safe defaults that work with standard AWS Textract quotas.

## Architecture

The stack includes:
- **Input S3 Bucket**: Upload original PDFs
- **Output S3 Bucket**: Download processed PDFs
- **Lambda Functions**: Process PDFs using Textract with quota management
- **DynamoDB Table**: Track processing logs
- **Lambda Layers**: Shared utilities and dependencies
- **Quota Management**: Built-in throttling and concurrent job limiting
