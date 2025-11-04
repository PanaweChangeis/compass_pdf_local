#!/bin/bash
# Script to run PDF analysis with proper directory setup

# Activate virtual environment
source .venv/bin/activate

# Ensure Cognee's storage directories exist
# Cognee uses both .cognee_system and .data_storage directories
mkdir -p .venv/lib/python3.12/site-packages/cognee/.cognee_system
mkdir -p .venv/lib/python3.12/site-packages/cognee/.cognee_system/databases
mkdir -p .venv/lib/python3.12/site-packages/cognee/.data_storage

# Run the analysis
python analyze_pdf.py
