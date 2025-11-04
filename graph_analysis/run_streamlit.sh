#!/bin/bash

# Launch Streamlit UI for PDF Analysis
# This script starts the Streamlit web interface

echo "🚀 Starting Streamlit UI for PDF Analysis..."
echo ""
echo "The web interface will open in your browser at http://localhost:8501"
echo ""
echo "Features:"
echo "  ✓ Interactive Q&A with your PDFs"
echo "  ✓ Clickable expandable sections for context"
echo "  ✓ View full LLM prompts"
echo "  ✓ Export conversation history"
echo "  ✓ Side-by-side Q&A and context display"
echo ""

uv run streamlit run streamlit_app.py
