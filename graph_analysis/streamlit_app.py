"""
Streamlit UI for PDF Analysis with Cognee
Interactive web interface for querying PDFs with conversation history and context visualization
"""

import asyncio
import json
import hashlib
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from io import BytesIO

# Load configuration from .env file BEFORE importing cognee
load_dotenv()

import cognee
import streamlit as st
from litellm.integrations.custom_logger import CustomLogger
import litellm
from pdf2image import convert_from_path
from PIL import Image
from PyPDF2 import PdfReader

# Page configuration
st.set_page_config(
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Global variable to store the last prompt sent to LLM
if 'last_llm_prompt' not in st.session_state:
    st.session_state.last_llm_prompt = None

# Disable LiteLLM's async callbacks to prevent event loop conflicts
litellm.callbacks = []
litellm.success_callback = []
litellm.failure_callback = []

# Monkey-patch litellm.completion to capture prompts without async callbacks
_original_completion = litellm.completion

def _patched_completion(*args, **kwargs):
    """Capture LLM prompts synchronously"""
    # Disable callbacks for this call
    kwargs['success_callback'] = []
    kwargs['failure_callback'] = []
    
    # Store the prompt details
    st.session_state.last_llm_prompt = {
        'model': kwargs.get('model', args[0] if args else 'unknown'),
        'messages': kwargs.get('messages', args[1] if len(args) > 1 else []),
        'kwargs': {k: v for k, v in kwargs.items() if k not in ['model', 'messages', 'success_callback', 'failure_callback']}
    }
    # Call the original function
    return _original_completion(*args, **kwargs)

# Apply the monkey patch
litellm.completion = _patched_completion

# Also patch acompletion for async calls
_original_acompletion = litellm.acompletion

async def _patched_acompletion(*args, **kwargs):
    """Capture LLM prompts synchronously for async calls"""
    # Disable callbacks for this call
    kwargs['success_callback'] = []
    kwargs['failure_callback'] = []
    
    # Store the prompt details
    st.session_state.last_llm_prompt = {
        'model': kwargs.get('model', args[0] if args else 'unknown'),
        'messages': kwargs.get('messages', args[1] if len(args) > 1 else []),
        'kwargs': {k: v for k, v in kwargs.items() if k not in ['model', 'messages', 'success_callback', 'failure_callback']}
    }
    # Call the original function
    return await _original_acompletion(*args, **kwargs)

# Apply the async monkey patch
litellm.acompletion = _patched_acompletion


def get_file_hash(file_path: Path) -> str:
    """Calculate MD5 hash of a file"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def load_processed_files() -> dict:
    """Load the record of processed files"""
    processed_file = Path(".processed_files.json")
    if processed_file.exists():
        with open(processed_file, "r") as f:
            return json.load(f)
    return {}


def save_processed_files(processed: dict):
    """Save the record of processed files"""
    with open(".processed_files.json", "w") as f:
        json.dump(processed, f, indent=2)


def extract_context_info(results: List[Any]) -> Dict[str, Any]:
    """Extract useful context information from cognee search results"""
    context = {
        'total_results': len(results),
        'graph_nodes': [],
        'raw_results': []
    }
    
    for idx, result in enumerate(results):
        node_info = {
            'index': idx + 1,
            'raw_type': type(result).__name__
        }
        
        # Extract all available attributes
        if hasattr(result, '__dict__'):
            for attr_name, attr_value in result.__dict__.items():
                if not attr_name.startswith('_'):
                    str_value = str(attr_value)
                    if len(str_value) > 2000:
                        str_value = str_value[:2000] + "... [truncated]"
                    node_info[attr_name] = str_value
        
        # Try common attributes
        for attr in ['name', 'type', 'description', 'text', 'content', 'answer']:
            if hasattr(result, attr):
                value = getattr(result, attr)
                if value and attr not in node_info:
                    str_value = str(value)
                    if len(str_value) > 2000:
                        str_value = str_value[:2000] + "... [truncated]"
                    node_info[attr] = str_value
        
        # Handle dict results
        if isinstance(result, dict):
            for key, value in result.items():
                if not key.startswith('_'):
                    str_value = str(value)
                    if len(str_value) > 2000:
                        str_value = str_value[:2000] + "... [truncated]"
                    node_info[key] = str_value
        
        # Store raw result
        raw_str = str(result)
        if len(raw_str) > 3000:
            raw_str = raw_str[:3000] + "... [truncated]"
        context['raw_results'].append(raw_str)
        
        # For string results
        if isinstance(result, str) and result.strip():
            node_info['content'] = result
        
        context['graph_nodes'].append(node_info)
    
    return context


def format_result(result) -> str:
    """Extract readable text from a search result"""
    if isinstance(result, str):
        return result
    
    # Try various attributes
    for attr in ['text', 'answer', 'content', 'description']:
        if hasattr(result, attr):
            value = getattr(result, attr)
            if value:
                return str(value)
    
    # Try dict keys
    if isinstance(result, dict):
        for key in ['text', 'answer', 'content', 'description']:
            if key in result and result[key]:
                return str(result[key])
    
    return str(result)


def extract_pdf_pages_with_metadata(pdf_path: Path) -> List[str]:
    """
    Extract text from PDF with page number annotations.
    Returns list of text chunks, each annotated with its page number.
    """
    try:
        reader = PdfReader(str(pdf_path))
        annotated_chunks = []
        
        for page_num, page in enumerate(reader.pages, start=1):
            # Extract text from the page
            text = page.extract_text()
            
            if text.strip():
                # Annotate the text with page number
                # Format: [Page X] content
                annotated_text = f"[Page {page_num}] {text.strip()}"
                annotated_chunks.append(annotated_text)
        
        return annotated_chunks
    except Exception as e:
        st.error(f"Error extracting PDF pages: {e}")
        return []


async def process_pdfs():
    """Process PDF files with cognee, preserving page metadata"""
    input_dir = Path("input")
    pdf_files = list(input_dir.glob("*.pdf"))
    
    if not pdf_files:
        st.error("❌ No PDF files found in input/ directory")
        return False
    
    processed_files = load_processed_files()
    new_files = []
    
    for pdf_path in pdf_files:
        file_hash = get_file_hash(pdf_path)
        file_name = pdf_path.name
        
        if file_name not in processed_files or processed_files[file_name] != file_hash:
            new_files.append((pdf_path, file_hash))
    
    if not new_files:
        st.success("✅ All files already processed!")
        return True
    
    with st.spinner(f"Processing {len(new_files)} PDF file(s)..."):
        for pdf_path, file_hash in new_files:
            st.info(f"📥 Extracting pages from: {pdf_path.name}")
            
            # Extract PDF pages with page number annotations
            annotated_chunks = extract_pdf_pages_with_metadata(pdf_path)
            
            if not annotated_chunks:
                st.warning(f"⚠️ No text extracted from {pdf_path.name}")
                continue
            
            st.info(f"📄 Extracted {len(annotated_chunks)} pages")
            
            # Add each annotated chunk to cognee
            # Sanitize dataset name (no spaces or dots allowed)
            dataset_name = pdf_path.stem.replace(" ", "_").replace(".", "_")
            for chunk in annotated_chunks:
                await cognee.add(chunk, dataset_name=dataset_name)
            
            processed_files[pdf_path.name] = file_hash
        
        st.info("🧠 Building knowledge graph...")
        await cognee.cognify()
        
        st.info("✨ Enriching with memify...")
        await cognee.memify()
        
        save_processed_files(processed_files)
    
    st.success("✅ Processing complete!")
    return True


async def search_query(query: str):
    """Execute search query"""
    st.session_state.last_llm_prompt = None
    results = await cognee.search(query, cognee.SearchType.GRAPH_COMPLETION)
    return results


def extract_page_references_from_text(text: str) -> List[int]:
    """Extract page numbers from text (e.g., 'page 301', 'p. 42', etc.)"""
    patterns = [
        r'page\s+(\d+)',
        r'p\.\s*(\d+)',
        r'pg\.\s*(\d+)',
        r'\(page\s+(\d+)\)',
    ]
    
    pages = set()
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            pages.add(int(match.group(1)))
    
    return sorted(list(pages))


def extract_page_references_from_context(context: Dict[str, Any]) -> Dict[int, float]:
    """
    Extract page references from the graph context and rank by relevance.
    Returns dict of {page_number: relevance_score}
    
    Relevance is calculated based on:
    - Position in graph results (earlier = more relevant)
    - Frequency of mentions
    - Length of content from that page
    """
    page_scores = {}
    
    # Check if we have graph nodes
    graph_nodes = context.get('graph_nodes', [])
    if not graph_nodes:
        # Fallback to LLM prompt extraction
        llm_prompt = context.get('llm_prompt', {})
        if llm_prompt and 'messages' in llm_prompt:
            for message in llm_prompt['messages']:
                content = message.get('content', '')
                pages = extract_page_references_from_text(content)
                for page in pages:
                    page_scores[page] = page_scores.get(page, 0) + 1.0
        return page_scores
    
    # Analyze graph nodes for page references and relevance
    total_nodes = len(graph_nodes)
    for idx, node in enumerate(graph_nodes):
        # Position score: earlier nodes are more relevant (inverse of position)
        position_score = (total_nodes - idx) / total_nodes
        
        # Extract page numbers from this node
        node_text = ""
        for key, value in node.items():
            if isinstance(value, str):
                node_text += value + " "
        
        pages = extract_page_references_from_text(node_text)
        
        # Content length score: more content = more relevant
        content_length = len(node_text)
        length_score = min(content_length / 1000, 1.0)  # Normalize to 0-1
        
        # Combined score for this node
        node_score = (position_score * 0.7) + (length_score * 0.3)
        
        # Add to page scores
        for page in pages:
            page_scores[page] = page_scores.get(page, 0) + node_score
    
    return page_scores


def get_pdf_page_image(pdf_path: Path, page_num: int) -> Optional[Image.Image]:
    """Convert a specific PDF page to an image"""
    try:
        images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=150)
        return images[0] if images else None
    except Exception as e:
        st.error(f"Error rendering page {page_num}: {e}")
        return None


def make_text_with_citations_clickable(text: str, entry_number: int) -> str:
    """Convert page references in text to clickable links"""
    patterns = [
        (r'(page\s+)(\d+)', r'\1[\2](#{entry_num}-page-\2)'),
        (r'(p\.\s*)(\d+)', r'\1[\2](#{entry_num}-page-\2)'),
        (r'(pg\.\s*)(\d+)', r'\1[\2](#{entry_num}-page-\2)'),
        (r'\((page\s+)(\d+)\)', r'([\1\2](#{entry_num}-page-\2))'),
    ]
    
    result = text
    for pattern, replacement in patterns:
        replacement_with_num = replacement.replace('{entry_num}', str(entry_number))
        result = re.sub(pattern, replacement_with_num, result, flags=re.IGNORECASE)
    
    return result


def display_prompt_messages(messages):
    """Display LLM prompt messages with syntax highlighting"""
    for i, message in enumerate(messages, 1):
        role = message.get('role', 'unknown').upper()
        content = message.get('content', '')
        
        with st.expander(f"📝 Message #{i}: {role}", expanded=(i == len(messages))):
            st.code(content, language="text")


def display_pdf_page_viewer(entry_number: int, page_num: int):
    """Display a PDF page in a viewer"""
    input_dir = Path("input")
    pdf_files = list(input_dir.glob("*.pdf"))
    
    if not pdf_files:
        st.error("No PDF files found")
        return
    
    # Use the first PDF file (could be enhanced to track which PDF each answer came from)
    pdf_path = pdf_files[0]
    
    with st.spinner(f"Loading page {page_num}..."):
        image = get_pdf_page_image(pdf_path, page_num)
        
        if image:
            st.image(image, caption=f"Page {page_num} from {pdf_path.name}", use_container_width=True)
        else:
            st.error(f"Could not load page {page_num}")


def main():
    st.title("📄 PDF Analysis with Cognee")
    
    # Initialize session state
    if 'conversation_history' not in st.session_state:
        st.session_state.conversation_history = []
    if 'processed' not in st.session_state:
        st.session_state.processed = False
    
    # Sidebar - more compact
    with st.sidebar:
        st.subheader("📚 Document Processing")
        if st.button("🔄 Process PDFs", use_container_width=True):
            asyncio.run(process_pdfs())
            st.session_state.processed = True
        
        st.divider()
        
        st.subheader("💾 History")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Q&A", len(st.session_state.conversation_history))
        with col2:
            if st.button("🗑️", use_container_width=True, help="Clear History"):
                st.session_state.conversation_history = []
                st.rerun()
        
        if st.session_state.conversation_history:
            # Create a serializable version of the history
            exportable_history = []
            for entry in st.session_state.conversation_history:
                exportable_entry = {
                    'number': entry['number'],
                    'question': entry['question'],
                    'answer': entry['answer'],
                    'timestamp': entry['timestamp'],
                    'model': entry.get('context', {}).get('llm_prompt', {}).get('model', 'Unknown')
                }
                exportable_history.append(exportable_entry)
            
            json_str = json.dumps(exportable_history, indent=2)
            st.download_button(
                label="📥 Export JSON",
                data=json_str,
                file_name=f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True
            )
    
    # Main content
    if not st.session_state.processed:
        st.info("👈 Process PDFs to get started")
        return
    
    # Query input - more compact
    query = st.text_input("Your question:", placeholder="What frequencies are test set spurs appearing at?", label_visibility="collapsed")
    search_button = st.button("🔍 Search", type="primary")
    
    if search_button and query:
        with st.spinner("Searching knowledge graph..."):
            results = asyncio.run(search_query(query))
            
            if not results:
                st.error("❌ No results found. Try rephrasing your question.")
            else:
                # Extract context
                context = extract_context_info(results)
                context['query'] = query
                context['search_type'] = 'GRAPH_COMPLETION'
                
                if st.session_state.last_llm_prompt:
                    context['llm_prompt'] = st.session_state.last_llm_prompt
                
                # Format answer
                seen_texts = set()
                formatted_parts = []
                for result in results:
                    text = format_result(result)
                    if text not in seen_texts:
                        seen_texts.add(text)
                        formatted_parts.append(text)
                
                answer = "\n\n".join(formatted_parts)
                
                # Add to history
                entry = {
                    'number': len(st.session_state.conversation_history) + 1,
                    'question': query,
                    'answer': answer,
                    'context': context,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                st.session_state.conversation_history.append(entry)
                st.rerun()
    
    # Display conversation history - redesigned layout
    if st.session_state.conversation_history:
        st.divider()
        
        # Reverse to show newest first
        for entry in reversed(st.session_state.conversation_history):
            # Extract page references from the graph context (not the answer)
            context = entry.get('context', {})
            page_counts = extract_page_references_from_context(context)
            
            # Create two equal columns: Q&A on left, Page viewer on right
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.markdown(f"**Q#{entry['number']}:** {entry['question']}")
                with st.expander("Answer", expanded=True):
                    # Display answer text
                    st.write(entry['answer'])
                    
                    # Add page reference catalog below answer (from graph context)
                    if page_counts:
                        st.markdown("---")
                        
                        # Sort by relevance score (higher = more relevant)
                        sorted_pages = sorted(page_counts.keys(), key=lambda p: (-page_counts[p], p))
                        
                        # Normalize scores to 0-100 for display
                        max_score = max(page_counts.values()) if page_counts else 1
                        
                        # Create dropdown options with relevance indicators
                        page_options = []
                        for rank, page_num in enumerate(sorted_pages, 1):
                            score = page_counts[page_num]
                            relevance_pct = int((score / max_score) * 100)
                            
                            # Determine indicator based on relative relevance
                            if relevance_pct >= 80:
                                indicator = "🔴"  # High relevance
                            elif relevance_pct >= 50:
                                indicator = "🟡"  # Medium relevance
                            else:
                                indicator = "⚪"  # Lower relevance
                            
                            page_options.append(f"{indicator} p.{page_num} ({relevance_pct}%)")
                        
                        # More concise dropdown (50% width)
                        col_dropdown, col_spacer = st.columns([1, 1])
                        with col_dropdown:
                            selected = st.selectbox(
                                "📚 Sources:",
                                options=["Select page..."] + page_options,
                                key=f"page-select-{entry['number']}",
                                label_visibility="visible"
                            )
                        
                        # Extract page number from selection and load it
                        if selected != "Select page...":
                            # Extract page number from format "🔴 p.301 (85%)"
                            page_num = int(selected.split("p.")[1].split(" ")[0])
                            st.session_state[f'show_page_{entry["number"]}'] = page_num
                            st.rerun()
                
                st.caption(f"🕐 {entry['timestamp']}")
                
                # Move LLM Prompt link here
                context = entry['context']
                llm_prompt = context.get('llm_prompt')
                
                if llm_prompt and 'messages' in llm_prompt:
                    with st.expander("🔍 View LLM Prompt", expanded=False):
                        st.caption(f"Model: {llm_prompt.get('model', 'Unknown').split('/')[-1]}")
                        display_prompt_messages(llm_prompt['messages'])
            
            with col2:
                # Display the selected page if any
                selected_page_key = f'show_page_{entry["number"]}'
                if selected_page_key in st.session_state:
                    page_to_show = st.session_state[selected_page_key]
                    st.markdown(f"**📄 Page {page_to_show}**")
                    display_pdf_page_viewer(entry['number'], page_to_show)
                else:
                    st.info("👈 Click a page reference to view the source")
            
            st.divider()


if __name__ == "__main__":
    main()
