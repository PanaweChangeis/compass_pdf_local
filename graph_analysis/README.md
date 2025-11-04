# Multi-Modal Document Analysis with Cognee

Fast, intelligent document analysis using AWS Textract data and multi-modal processing (text + images).

## Quick Start

### 1. Setup

```bash
# Install dependencies
uv venv
uv pip install -e .

# Configure environment
cp .env.example .env
# Edit .env with your OpenRouter API key
```

### 2. Configure AWS SSO

```bash
# One-time setup
aws configure sso --profile my-profile

# Login (before each session)
aws sso login --profile my-profile
```

### 3. Run the App

```bash
# With specific AWS profile
./run_streamlit_multimodal.sh my-profile

# Or with default profile
./run_streamlit_multimodal.sh
```

The app will:
- ✅ Auto-discover your S3 bucket and DynamoDB table
- ✅ List all available documents
- ✅ Load documents with text + images (10x faster than PDF)
- ✅ Enable intelligent Q&A

## How It Works

```
AWS Textract JSON + Page Images (S3)
    ↓
Auto-Discovery (by AWS profile)
    ↓
Multi-Modal Loading (text + visual)
    ↓
Cognee Knowledge Graph
    ↓
Intelligent Q&A
```

## Configuration

### Environment Variables (.env)

```bash
# LLM Configuration (Required)
LLM_API_KEY=sk-or-v1-your-openrouter-key
LLM_MODEL=openai/anthropic/claude-sonnet-4.5
LLM_ENDPOINT=https://openrouter.ai/api/v1

# AWS Configuration (Optional - auto-discovered)
AWS_PROFILE=my-profile
S3_PROCESSED_BUCKET=auto-discovered
DDB_DOCUMENTS_TABLE=auto-discovered
AWS_REGION=auto-discovered
```

### AWS Resources

The app automatically discovers:
- **S3 Bucket**: Looks for `*processeddocuments*` pattern
- **DynamoDB Table**: Looks for `*documents*` with correct schema
- **Region**: From your AWS profile

To override auto-discovery:
```bash
export S3_PROCESSED_BUCKET=my-bucket-name
export DDB_DOCUMENTS_TABLE=my-table-name
```

## Usage

### In the Streamlit App

1. **Select Document**: Choose from dropdown (auto-populated)
2. **Choose Mode**: 
   - Multi-Modal (Text + Images) - Recommended
   - Text Only - Faster, no visual understanding
3. **Load Document**: Click "Load Document"
4. **Ask Questions**: Natural language queries

### Query Examples

```
"What are the installation steps?"
"Show me the wiring diagram"
"List all part numbers"
"Explain the components in the schematic"
```

## Troubleshooting

### AWS Credentials

**Error**: `AWS credentials not valid`

**Solution**:
```bash
aws sso login --profile my-profile
```

### Resources Not Found

**Error**: `No processed documents bucket found`

**Solutions**:
1. Check bucket name matches pattern (`*processeddocuments*`)
2. Verify AWS permissions
3. Manual override:
   ```bash
   export S3_PROCESSED_BUCKET=actual-bucket-name
   export DDB_DOCUMENTS_TABLE=actual-table-name
   ```

### Session Expired

**Error**: `ExpiredToken`

**Solution**:
```bash
aws sso login --profile my-profile
```

## Architecture

### Components

- **streamlit_multimodal.py**: Main UI application
- **aws_config.py**: Auto-discovery of AWS resources
- **document_lookup.py**: DynamoDB queries for documents
- **textract_loader.py**: Converts Textract JSON to markdown + loads images
- **run_streamlit_multimodal.sh**: Launch script with AWS profile support

### Data Flow

```
1. User runs script with AWS profile
2. App discovers S3 bucket & DynamoDB table
3. Lists available documents from DynamoDB
4. User selects document
5. Loads Textract JSON → markdown text
6. Downloads page images from S3
7. Processes with Cognee (text + images)
8. Ready for Q&A
```

## Benefits

- ⚡ **10x Faster**: No PDF parsing
- 🖼️ **Visual Understanding**: Analyzes diagrams and schematics
- 🔐 **Secure**: Uses AWS SSO temporary credentials
- 🔄 **Multi-Account**: Easy profile switching
- 🚀 **Zero Config**: Auto-discovers resources

## Advanced

### Multiple AWS Profiles

```bash
# Development
./run_streamlit_multimodal.sh dev-profile

# Production
./run_streamlit_multimodal.sh prod-profile
```

### Custom Configuration

```bash
# Override auto-discovery
export S3_PROCESSED_BUCKET=custom-bucket
export DDB_DOCUMENTS_TABLE=custom-table
export AWS_REGION=us-west-2

./run_streamlit_multimodal.sh
```

## Support

For issues or questions:
1. Check AWS credentials: `aws sts get-caller-identity`
2. Verify resources exist in AWS console
3. Check logs in terminal output
