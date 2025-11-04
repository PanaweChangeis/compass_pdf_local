#!/bin/bash

# Script to start LiteLLM proxy with AWS_PROFILE configured
# Usage: ./start_proxy.sh [profile-name]

set -e

# Check if profile name is provided
if [ -z "$1" ]; then
    echo "Usage: ./start_proxy.sh <aws-profile-name>"
    echo ""
    echo "Example:"
    echo "  ./start_proxy.sh my-aws-profile"
    echo ""
    echo "Or if using default profile:"
    echo "  ./start_proxy.sh default"
    exit 1
fi

PROFILE_NAME=$1

echo "🔧 Setting up LiteLLM proxy with AWS profile: $PROFILE_NAME"
echo ""

# Verify AWS profile exists and works
echo "📋 Verifying AWS profile..."
if aws sts get-caller-identity --profile "$PROFILE_NAME" --no-verify-ssl > /dev/null 2>&1; then
    echo "✅ AWS profile '$PROFILE_NAME' is valid"
    aws sts get-caller-identity --profile "$PROFILE_NAME" --no-verify-ssl
else
    echo "❌ AWS profile '$PROFILE_NAME' is not valid or not configured"
    echo ""
    echo "To configure AWS SSO:"
    echo "  aws sso login --profile $PROFILE_NAME"
    echo ""
    echo "Or configure credentials:"
    echo "  aws configure --profile $PROFILE_NAME"
    exit 1
fi

echo ""
echo "🚀 Starting LiteLLM proxy..."
echo "   Profile: $PROFILE_NAME"
echo "   Config: config.yml"
echo "   URL: http://localhost:4000"
echo ""
echo "Press Ctrl+C to stop the proxy"
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate virtual environment if it exists
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# Disable SSL verification for Python requests (needed in corporate environments)
export REQUESTS_CA_BUNDLE=""
export CURL_CA_BUNDLE=""
export PYTHONHTTPSVERIFY=0

# Export AWS_PROFILE and start litellm
export AWS_PROFILE="$PROFILE_NAME"
litellm --config config.yml
