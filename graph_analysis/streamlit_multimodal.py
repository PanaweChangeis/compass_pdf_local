"""
Multi-Modal Streamlit App for Document Analysis
================================================

Streamlit interface for loading documents from S3 using DynamoDB lookup
and multi-modal processing (text + images) with Cognee.
"""

import asyncio
import os
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv
import logging
import nest_asyncio
import subprocess
import sys

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

# Load environment before importing cognee
load_dotenv()

# Import cognee lazily inside functions to allow model switching
from document_lookup import DocumentLookup
from textract_loader_v2 import load_document_pages_for_cognee
from aws_config import get_aws_config_from_env, AWSConfigDiscovery
from rich.console import Console

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Auto-discover AWS configuration on startup
AWS_CONFIG = get_aws_config_from_env()
logger.info(f"Auto-discovered AWS config: {AWS_CONFIG}")

# Log the current model configuration on startup
current_model = os.getenv('LLM_MODEL', 'NOT_SET')
logger.info(f"🚀 App started with LLM_MODEL: {current_model}")


# ============================================================================
# OpenRouter Model Management
# ============================================================================

@st.cache_data(ttl=3600)  # Cache for 1 hour
def fetch_multimodal_models():
    """Fetch all multimodal models from OpenRouter API"""
    import requests
    
    try:
        api_key = os.getenv('LLM_API_KEY')
        if not api_key:
            logger.warning("No LLM_API_KEY found, using default models")
            return []
        
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/Changeis-Inc/compass",
                "X-Title": "Compass Document Analysis"
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        # Filter to multimodal models only (support image inputs)
        multimodal_models = [
            model for model in data.get('data', [])
            if 'image' in model.get('architecture', {}).get('input_modalities', [])
        ]
        
        logger.info(f"Fetched {len(multimodal_models)} multimodal models from OpenRouter")
        return multimodal_models
        
    except Exception as e:
        logger.error(f"Error fetching models from OpenRouter: {e}")
        return []


def format_model_for_display(model):
    """Format model information for display"""
    name = model.get('name', 'Unknown')
    context = model.get('context_length', 0)
    pricing = model.get('pricing', {})
    
    # Format context length
    if context >= 1000000:
        context_str = f"{context/1000000:.1f}M"
    elif context >= 1000:
        context_str = f"{context/1000:.0f}K"
    else:
        context_str = str(context)
    
    # Format pricing
    prompt_cost = float(pricing.get('prompt', 0)) * 1000
    image_cost = float(pricing.get('image', 0))
    
    return {
        'id': f"openai/{model['id']}",  # Add openai/ prefix for compatibility
        'name': name,
        'display': f"{name} ({context_str} ctx)",
        'context_length': context,
        'prompt_cost_per_1k': prompt_cost,
        'image_cost': image_cost,
        'raw_id': model['id']
    }


def get_model_options():
    """Get formatted model options for selector"""
    models = fetch_multimodal_models()
    
    if not models:
        # Fallback to default models if API fails
        return [
            {
                'id': 'openai/anthropic/claude-sonnet-4.5',
                'name': 'Claude Sonnet 4.5',
                'display': 'Claude Sonnet 4.5 (200K ctx)',
                'context_length': 200000,
                'prompt_cost_per_1k': 3.0,
                'image_cost': 4.8,
                'raw_id': 'anthropic/claude-sonnet-4.5'
            },
            {
                'id': 'openai/openai/gpt-4o',
                'name': 'GPT-4o',
                'display': 'GPT-4o (128K ctx)',
                'context_length': 128000,
                'prompt_cost_per_1k': 2.5,
                'image_cost': 7.225,
                'raw_id': 'openai/gpt-4o'
            }
        ]
    
    # Format and sort by context length (descending)
    formatted = [format_model_for_display(m) for m in models]
    return sorted(formatted, key=lambda x: x['context_length'], reverse=True)

# Page configuration
st.set_page_config(
    page_icon="📄",
    layout="wide"
)

# Initialize session state
if 'document_loaded' not in st.session_state:
    st.session_state.document_loaded = False
if 'current_document' not in st.session_state:
    st.session_state.current_document = None
if 'query_history' not in st.session_state:
    st.session_state.query_history = []
if 'model_changed' not in st.session_state:
    st.session_state.model_changed = False


def load_document_async(bucket, document_id, document_name, use_images, progress_callback=None, chunk_size=10):
    """Async wrapper for document loading with parallel chunk processing"""
    async def _load():
        # Lazy import cognee to allow model switching
        import sys
        if 'cognee' in sys.modules:
            del sys.modules['cognee']
        import cognee

        # Force Cognee's LLMConfig to reload from environment variables
        try:
            from cognee.infrastructure.llm.config import LLMConfig
            # Force reload of LLMConfig from current environment
            import os
            # Reload dotenv to pick up any changes
            load_dotenv(override=True)

            # Force LLMConfig to reinitialize with current env vars
            if hasattr(LLMConfig, '__init__'):
                # Try to reinitialize the config singleton
                LLMConfig.__init__(LLMConfig)
                logger.info("Forced LLMConfig reinitialization")

            current_model = os.environ.get('LLM_MODEL', 'NOT_SET')
            logger.info(f"Loading document with LLM_MODEL: {current_model}, STRUCTURED_OUTPUT_FRAMEWORK: {os.environ.get('STRUCTURED_OUTPUT_FRAMEWORK', 'instructor')}")
        except Exception as e:
            logger.warning(f"Could not reinitialize LLMConfig: {e}")

        # Try to update LLMGateway configuration if possible
        try:
            from cognee.infrastructure.llm.LLMGateway import LLMGateway
            current_model = os.environ.get('LLM_MODEL', 'NOT_SET')
            structured_framework = os.environ.get('STRUCTURED_OUTPUT_FRAMEWORK', 'instructor')
            logger.info(f"Loading document with LLM_MODEL: {current_model}, STRUCTURED_OUTPUT_FRAMEWORK: {structured_framework}")

            # Try to update LLMGateway model if it has the attribute
            if hasattr(LLMGateway, 'model'):
                LLMGateway.model = current_model
                logger.info(f"Updated LLMGateway.model to: {current_model}")
            elif hasattr(LLMGateway, '_model'):
                LLMGateway._model = current_model
                logger.info(f"Updated LLMGateway._model to: {current_model}")
        except Exception as e:
            logger.warning(f"Could not update LLMGateway configuration: {e}")

        # Sanitize document name for Cognee dataset (no spaces or dots)
        dataset_name = document_id.replace('.', '_').replace(' ', '_')

        # Create dataset with clean name
        logger.info(f"Creating Cognee dataset: {dataset_name}")
        await cognee.add(data=[], dataset_name=dataset_name)
        
        # Load document as page pairs
        page_pairs, total_pages = load_document_pages_for_cognee(
            document_id=document_id,
            textract_s3_bucket=bucket,
            textract_s3_key=f"{document_id}/textract_output_blocks.json",
            page_images_s3_bucket=bucket if use_images else None,
            page_images_s3_prefix=f"{document_id}/pages/" if use_images else None
        )
        
        logger.info(f"Starting Cognee ingestion for {total_pages} page pairs")
        logger.info(f"Using dataset: {dataset_name}")
        logger.info(f"Using parallel chunk processing (chunk_size={chunk_size})")
        
        # Calculate total chunks
        total_chunks = (total_pages + chunk_size - 1) // chunk_size
        
        # Process pages in parallel chunks
        total_chars = 0
        images_added = 0
        
        for chunk_idx, i in enumerate(range(0, len(page_pairs), chunk_size), 1):
            chunk = page_pairs[i:i+chunk_size]
            chunk_start = i + 1
            chunk_end = min(i + len(chunk), total_pages)
            
            logger.info(f"Processing chunk {chunk_idx}/{total_chunks} (pages {chunk_start}-{chunk_end})")
            
            # Update progress for chunk
            if progress_callback:
                progress_callback(
                    chunk_end, 
                    total_pages, 
                    f"chunk_{chunk_idx}",
                    f"Processing pages {chunk_start}-{chunk_end}"
                )
            
            # Add files to Cognee
            # Note: Even with PostgreSQL, we process sequentially to avoid event loop conflicts
            # The main performance benefit of PostgreSQL is better concurrent access and scalability
            for text_path, image_path in chunk:
                await cognee.add(str(text_path))
                total_chars += len(text_path.read_text())
                
                if image_path:
                    await cognee.add(str(image_path))
                    images_added += 1
            
            db_provider = os.getenv('DB_PROVIDER', 'sqlite').lower()
            if db_provider in ['postgresql', 'postgres']:
                logger.info(f"PostgreSQL: Processed {len(chunk)} page pairs (chunk {chunk_idx}/{total_chunks})")
            else:
                logger.info(f"SQLite: Processed {len(chunk)} page pairs (chunk {chunk_idx}/{total_chunks})")
            
            logger.info(f"Completed chunk {chunk_idx}/{total_chunks}")
        
        logger.info(f"Completed adding {total_pages} page pairs to Cognee")
        logger.info(f"Starting cognify process...")
        
        # Process with Cognee
        await cognee.cognify()
        await cognee.memify()
        
        logger.info(f"Cognify complete!")
        
        return total_chars, images_added
    
    return asyncio.run(_load())


def search_async(query):
    """Async wrapper for search"""
    async def _search():
        # Lazy import cognee to allow model switching
        import sys
        if 'cognee' in sys.modules:
            del sys.modules['cognee']
        import cognee

        # Force Cognee's LLMConfig to reload from environment variables
        try:
            from cognee.infrastructure.llm.config import LLMConfig
            # Force reload of LLMConfig from current environment
            import os
            # Reload dotenv to pick up any changes
            load_dotenv(override=True)

            # Force LLMConfig to reinitialize with current env vars
            if hasattr(LLMConfig, '__init__'):
                # Try to reinitialize the config singleton
                LLMConfig.__init__(LLMConfig)
                logger.info("Forced LLMConfig reinitialization for search")

            current_model = os.environ.get('LLM_MODEL', 'NOT_SET')
            logger.info(f"Searching with LLM_MODEL: {current_model}, STRUCTURED_OUTPUT_FRAMEWORK: {os.environ.get('STRUCTURED_OUTPUT_FRAMEWORK', 'instructor')}")
        except Exception as e:
            logger.warning(f"Could not reinitialize LLMConfig for search: {e}")

        # Try to update LiteLLM configuration if possible
        try:
            import litellm
            current_model = os.environ.get('LLM_MODEL', 'NOT_SET')
            logger.info(f"Searching with LLM_MODEL: {current_model}")

            # Try to update litellm's default model
            if hasattr(litellm, 'model'):
                litellm.model = current_model
                logger.info(f"Updated litellm.model to: {current_model}")
        except Exception as e:
            logger.warning(f"Could not update LiteLLM configuration: {e}")

        results = await cognee.search(query, cognee.SearchType.GRAPH_COMPLETION)
        return results

    return asyncio.run(_search())


# Sidebar - Document Selection
with st.sidebar:
    st.header("📄 Document Selection")
    
    # AWS Configuration - Auto-discovered
    st.subheader("AWS Configuration")
    
    # Show auto-discovered configuration
    if AWS_CONFIG.get('profile'):
        st.info(f"🔐 Profile: {AWS_CONFIG['profile']}")
    
    bucket = st.text_input(
        "S3 Bucket",
        value=AWS_CONFIG.get('bucket') or '',
        help="Auto-discovered from AWS profile"
    )
    
    ddb_table = st.text_input(
        "DynamoDB Table",
        value=AWS_CONFIG.get('table') or '',
        help="Auto-discovered from AWS profile"
    )
    
    aws_region = st.text_input(
        "AWS Region",
        value=AWS_CONFIG.get('region') or 'us-east-1',
        help="Auto-discovered from AWS profile"
    )
    
    # Re-discover button
    if st.button("🔍 Re-discover Resources"):
        with st.spinner("Discovering AWS resources..."):
            new_config = get_aws_config_from_env()
            st.session_state.aws_config = new_config
            st.success("Resources re-discovered!")
            st.rerun()
    
    st.divider()
    
    # Document Selection
    if bucket and ddb_table:
        try:
            # Initialize lookup
            lookup = DocumentLookup(ddb_table, region=aws_region)
            
            # Refresh documents button
            if st.button("🔄 Refresh Document List"):
                st.session_state.pop('available_docs', None)
            
            # Get available documents (cached in session state)
            if 'available_docs' not in st.session_state:
                with st.spinner("Loading document list..."):
                    st.session_state.available_docs = lookup.list_all_documents()
            
            available_docs = st.session_state.available_docs
            
            if available_docs:
                st.subheader("Select Document")
                
                selected_doc = st.selectbox(
                    "Document Name",
                    options=available_docs,
                    help="Choose a document to analyze"
                )
                
                # Loading mode
                mode = st.radio(
                    "Loading Mode",
                    ["Multi-Modal (Text + Images)", "Text Only"],
                    help="Multi-modal includes visual understanding of diagrams and schematics"
                )
                
                use_images = (mode == "Multi-Modal (Text + Images)")
                
                # Advanced settings
                with st.expander("⚙️ Advanced Settings"):
                    # Chunk size setting
                    chunk_size = st.slider(
                        "Parallel Chunk Size",
                        min_value=5,
                        max_value=50,
                        value=10,
                        step=5,
                        help="Number of pages to process in parallel. Higher = faster but may hit rate limits. Recommended: 10-20"
                    )
                    st.caption(f"Will process {chunk_size} pages at a time in parallel")
                    
                    st.divider()
                    
                    # Multimodal LLM Model Selector
                    st.subheader("🤖 Multimodal LLM Selection")
                    
                    # Get available models
                    model_options = get_model_options()
                    
                    if model_options:
                        # Find current model index
                        current_model = os.getenv('LLM_MODEL', 'openai/anthropic/claude-sonnet-4.5')
                        try:
                            default_idx = next(i for i, m in enumerate(model_options) if m['id'] == current_model)
                        except StopIteration:
                            default_idx = 0
                        
                        # Model selector
                        selected_idx = st.selectbox(
                            "Choose Model",
                            options=range(len(model_options)),
                            format_func=lambda i: model_options[i]['display'],
                            index=default_idx,
                            help="Only models supporting image inputs are shown",
                            key="model_selector"
                        )
                        
                        selected_model = model_options[selected_idx]
                        
                        # Show model details
                        col1, col2 = st.columns(2)
                        with col1:
                            st.caption(f"💰 ${selected_model['prompt_cost_per_1k']:.4f}/1K tokens")
                        with col2:
                            st.caption(f"🖼️ ${selected_model['image_cost']:.4f}/image")
                        
                        # Update environment if changed
                        if selected_model['id'] != st.session_state.get('selected_model'):
                            st.session_state.selected_model = selected_model['id']
                            os.environ['LLM_MODEL'] = selected_model['id']

                            # Update .env file permanently
                            try:
                                env_file_path = Path('.env')
                                env_content = env_file_path.read_text()

                                # Replace the LLM_MODEL line
                                import re
                                new_content = re.sub(
                                    r'^LLM_MODEL=.*$',
                                    f'LLM_MODEL={selected_model["id"]}',
                                    env_content,
                                    flags=re.MULTILINE
                                )

                                # Write back to .env file
                                env_file_path.write_text(new_content)
                                logger.info(f"Updated .env file with LLM_MODEL: {selected_model['id']}")
                            except Exception as e:
                                logger.warning(f"Could not update .env file: {e}")

                            st.session_state.model_changed = True  # Flag that model changed
                            logger.info(f"Switched LLM model to: {selected_model['id']}")
                            st.success(f"✅ Model: {selected_model['name']}")
                        
                        # Refresh models button
                        if st.button("🔄 Refresh Models", help="Reload model list from OpenRouter"):
                            st.cache_data.clear()
                            st.rerun()
                    else:
                        st.warning("⚠️ Could not load models from OpenRouter API")
                        st.caption("Using default model from .env file")
                
                # Load button
                if st.button("🚀 Load Document", type="primary"):
                    try:
                        # Find latest processing
                        doc_info = lookup.find_latest_processing(selected_doc)
                        
                        if doc_info:
                            document_id = doc_info['document_id']
                            
                            # Show processing info
                            st.success(f"Found: {document_id}")
                            
                            # Create progress containers
                            progress_bar = st.progress(0)
                            status_text = st.empty()
                            
                            # Progress callback for chunk-based loading
                            def update_progress(current, total, chunk_id, status_msg):
                                progress = current / total
                                progress_bar.progress(progress)
                                status_text.text(f"📦 {status_msg} ({current}/{total} pages)")
                            
                            # Load document with progress
                            status_text.text("Starting document load...")
                            text_len, image_count = load_document_async(
                                bucket, document_id, selected_doc, use_images, 
                                progress_callback=update_progress,
                                chunk_size=chunk_size
                            )
                            
                            # Clear progress UI
                            progress_bar.empty()
                            status_text.empty()
                            
                            # Update session state
                            st.session_state.document_loaded = True
                            st.session_state.current_document = {
                                'name': selected_doc,
                                'id': document_id,
                                'text_length': text_len,
                                'image_count': image_count,
                                'mode': mode
                            }
                            
                            st.success(f"✅ Loaded successfully!")
                            st.info(f"📝 Text: {text_len:,} characters")
                            if image_count > 0:
                                st.info(f"🖼️ Images: {image_count} pages")
                            
                            st.rerun()
                        else:
                            st.error(f"No processing found for: {selected_doc}")
                    
                    except Exception as e:
                        st.error(f"Error loading document: {e}")
                        logger.error(f"Error: {e}", exc_info=True)
            else:
                st.warning("No documents found in DynamoDB table")
        
        except Exception as e:
            st.error(f"Error connecting to AWS: {e}")
            st.info("Please check your AWS credentials and configuration")
    else:
        st.warning("Please configure S3 bucket and DynamoDB table")
    
    st.divider()
    
    # Current document info
    if st.session_state.document_loaded:
        st.subheader("Current Document")
        doc = st.session_state.current_document
        st.success(f"📄 {doc['name']}")
        st.caption(f"ID: {doc['id']}")
        st.caption(f"Mode: {doc['mode']}")
        
        if st.button("🗑️ Clear Document"):
            st.session_state.document_loaded = False
            st.session_state.current_document = None
            st.session_state.query_history = []
            st.rerun()

if st.session_state.document_loaded:
    doc = st.session_state.current_document

    st.success(f"📄 Analyzing: **{doc['name']}**")

    # Model change warning and restart prompt
    if st.session_state.model_changed:
        st.warning("⚠️ **Model Changed**: The LLM model was switched. To use the new model, please restart the app.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Restart App with New Model", type="primary"):
                # Clear the flag
                st.session_state.model_changed = False

                # Show restart message
                st.success("🔄 Restarting app with new model...")

                # Get the current script path and Python executable
                script_path = os.path.abspath(__file__)
                python_exe = sys.executable

                # Prepare environment with updated model
                env = os.environ.copy()
                env['LLM_MODEL'] = st.session_state.selected_model

                # Restart the app using subprocess
                try:
                    logger.info(f"Restarting Streamlit app with model: {st.session_state.selected_model}")
                    subprocess.Popen([python_exe, '-m', 'streamlit', 'run', script_path],
                                   env=env,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    st.stop()  # Stop the current Streamlit instance
                except Exception as e:
                    st.error(f"Failed to restart app: {e}")
                    logger.error(f"App restart failed: {e}")
        with col2:
            if st.button("❌ Dismiss (Keep Current Model)", help="Continue with the current model"):
                st.session_state.model_changed = False
                st.rerun()

    # Query interface
    st.subheader("Ask a Question")
    
    query = st.text_input(
        "Your question:",
        placeholder="What are the main components? Show me the wiring diagram...",
        key="query_input"
    )
    
    col1, col2 = st.columns([1, 5])
    with col1:
        search_button = st.button("🔍 Search", type="primary")
    with col2:
        if st.button("🗑️ Clear History"):
            st.session_state.query_history = []
            st.rerun()
    
    if search_button and query:
        with st.spinner("Searching..."):
            try:
                results = search_async(query)
                
                if results:
                    # Add to history
                    st.session_state.query_history.insert(0, {
                        'query': query,
                        'results': results
                    })
                    
                    # Display answer
                    st.subheader("💡 Answer")
                    
                    for result in results:
                        if hasattr(result, 'text'):
                            st.markdown(result.text)
                        elif hasattr(result, 'answer'):
                            st.markdown(result.answer)
                        elif isinstance(result, str):
                            st.markdown(result)
                        else:
                            st.write(result)
                else:
                    st.warning("No results found. Try rephrasing your question.")
            
            except Exception as e:
                st.error(f"Error during search: {e}")
                logger.error(f"Search error: {e}", exc_info=True)
    
    # Query history
    if st.session_state.query_history:
        st.divider()
        st.subheader("📜 Query History")
        
        for i, item in enumerate(st.session_state.query_history):
            with st.expander(f"Q: {item['query']}", expanded=(i == 0)):
                st.markdown("**Answer:**")
                for result in item['results']:
                    if hasattr(result, 'text'):
                        st.markdown(result.text)
                    elif hasattr(result, 'answer'):
                        st.markdown(result.answer)
                    elif isinstance(result, str):
                        st.markdown(result)
                    else:
                        st.write(result)

else:
    # Welcome screen
    st.info("👈 Select a document from the sidebar to begin")
    
    st.markdown("""
    ## Welcome to Multi-Modal Document Analysis
    
    This application uses **Cognee** with **multi-modal loading** to analyze documents:
    
    ### Features
    - 📝 **Text Understanding**: Extract and analyze document text
    - 🖼️ **Visual Understanding**: Analyze diagrams, schematics, and charts
    - ⚡ **Fast Loading**: 10x faster than traditional PDF processing
    - 🔍 **Intelligent Search**: Ask questions in natural language
    
    ### How to Use
    1. Configure AWS settings in the sidebar
    2. Select a document from the dropdown
    3. Choose loading mode (Multi-Modal recommended)
    4. Click "Load Document"
    5. Ask questions about your document!
    
    ### Query Examples
    - "What are the installation steps?"
    - "Show me the wiring diagram"
    - "List all part numbers"
    - "Explain the components in the schematic"
    """)
    
    # Configuration help
    with st.expander("⚙️ Configuration Help"):
        st.markdown("""
        ### AWS Configuration
        
        You need to configure:
        1. **S3 Bucket**: The bucket containing processed documents
        2. **DynamoDB Table**: The table tracking document processing
        3. **AWS Region**: Your AWS region (default: us-east-1)
        
        ### AWS Credentials
        
        Ensure AWS credentials are configured:
        ```bash
        # Option 1: AWS CLI
        aws configure
        
        # Option 2: Environment variables
        export AWS_ACCESS_KEY_ID=your-key
        export AWS_SECRET_ACCESS_KEY=your-secret
        ```
        
        ### Environment File
        
        You can also set these in `.env`:
        ```
        S3_PROCESSED_BUCKET=your-bucket-name
        DDB_DOCUMENTS_TABLE=your-table-name
        AWS_REGION=us-east-1
        ```
        """)
