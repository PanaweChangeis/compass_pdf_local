#!/bin/bash

# Run Multi-Modal Streamlit App with AWS SSO Profile
# ===================================================
# 
# This script runs the Streamlit app with multi-modal document loading
# from S3 using DynamoDB lookup. It automatically discovers AWS resources
# based on the current AWS profile.
#
# Usage:
#   ./run_streamlit_multimodal.sh [profile-name]
#
# Examples:
#   ./run_streamlit_multimodal.sh              # Use default profile
#   ./run_streamlit_multimodal.sh my-profile   # Use specific profile

# Change to script directory
cd "$(dirname "$0")"

# Get AWS profile from argument or use default
AWS_PROFILE_ARG="${1:-}"

if [ -n "$AWS_PROFILE_ARG" ]; then
    echo "Using AWS profile: $AWS_PROFILE_ARG"
    export AWS_PROFILE="$AWS_PROFILE_ARG"
else
    if [ -n "$AWS_PROFILE" ]; then
        echo "Using AWS profile from environment: $AWS_PROFILE"
    else
        echo "Using default AWS profile"
    fi
fi

# Validate AWS credentials
echo "Validating AWS credentials..."
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "ERROR: AWS credentials not valid or not configured"
    echo ""
    echo "Please configure AWS credentials first:"
    echo "  aws configure sso --profile <profile-name>"
    echo "  aws sso login --profile <profile-name>"
    echo ""
    echo "Then run this script with:"
    echo "  ./run_streamlit_multimodal.sh <profile-name>"
    exit 1
fi

# Show current AWS identity
echo "AWS Identity:"
aws sts get-caller-identity

# Activate virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "ERROR: Virtual environment not found"
    echo "Please run: uv venv && uv pip install -e ."
    exit 1
fi

# Run Streamlit app
echo ""
echo "Starting Streamlit app..."
echo "AWS resources will be auto-discovered based on your profile"
echo ""
streamlit run streamlit_multimodal.py
