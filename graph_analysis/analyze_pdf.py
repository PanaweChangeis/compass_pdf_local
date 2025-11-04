"""
PDF Analysis with Cognee
This script demonstrates cognee's capabilities by processing a PDF document
and enabling interactive querying with conversation history tracking.
"""

import asyncio
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv

# Load configuration from .env file BEFORE importing cognee
# This sets up LiteLLM proxy and Qdrant configuration
load_dotenv()

# Now import cognee after environment is configured
import cognee

# Rich library for beautiful terminal output
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.markdown import Markdown

# LiteLLM for capturing prompts
from litellm import completion
from litellm.integrations.custom_logger import CustomLogger

console = Console()

# Global variable to store the last prompt sent to LLM
last_llm_prompt = None


class PromptCaptureLogger(CustomLogger):
    """Custom logger to capture LLM prompts"""
    
    def log_pre_api_call(self, model, messages, kwargs):
        """Called before API call - capture the prompt"""
        global last_llm_prompt
        last_llm_prompt = {
            'model': model,
            'messages': messages,
            'kwargs': kwargs
        }
    
    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        """Called after API call"""
        pass
    
    def log_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Called for streaming events"""
        pass
    
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Called on successful completion"""
        pass
    
    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Called on failure"""
        pass


# Register the custom logger
import litellm
litellm.callbacks = [PromptCaptureLogger()]


class ConversationHistory:
    """Manages conversation history with Q&A and context tracking"""
    
    def __init__(self):
        self.history: List[Dict[str, Any]] = []
    
    def add_entry(self, question: str, answer: str, context: Dict[str, Any]):
        """Add a new Q&A entry with context"""
        entry = {
            'number': len(self.history) + 1,
            'question': question,
            'answer': answer,
            'context': context,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        self.history.append(entry)
    
    def get_entry(self, number: int) -> Dict[str, Any]:
        """Get a specific entry by number"""
        if 1 <= number <= len(self.history):
            return self.history[number - 1]
        return None
    
    def clear(self):
        """Clear all history"""
        self.history = []
    
    def export_to_json(self, filepath: str):
        """Export history to JSON file"""
        with open(filepath, 'w') as f:
            json.dump(self.history, f, indent=2)


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
        
        # Extract all available attributes from the result object
        if hasattr(result, '__dict__'):
            for attr_name, attr_value in result.__dict__.items():
                if not attr_name.startswith('_'):  # Skip private attributes
                    # Convert to string and limit length
                    str_value = str(attr_value)
                    if len(str_value) > 1000:
                        str_value = str_value[:1000] + "... [truncated]"
                    node_info[attr_name] = str_value
        
        # Also try common attribute names directly
        for attr in ['name', 'type', 'description', 'text', 'content', 'answer', 
                     'entity_type', 'relationship', 'properties', 'metadata']:
            if hasattr(result, attr):
                value = getattr(result, attr)
                if value and attr not in node_info:
                    str_value = str(value)
                    if len(str_value) > 1000:
                        str_value = str_value[:1000] + "... [truncated]"
                    node_info[attr] = str_value
        
        # Try to extract from dict
        if isinstance(result, dict):
            for key, value in result.items():
                if not key.startswith('_'):
                    str_value = str(value)
                    if len(str_value) > 1000:
                        str_value = str_value[:1000] + "... [truncated]"
                    node_info[key] = str_value
        
        # Store complete raw result for full transparency
        raw_str = str(result)
        if len(raw_str) > 2000:
            raw_str = raw_str[:2000] + "... [truncated]"
        context['raw_results'].append(raw_str)
        
        # For string results, add the text as content
        if isinstance(result, str) and result.strip():
            node_info['content'] = result
        
        # Always add the node info (even if minimal) so we show what was passed to LLM
        context['graph_nodes'].append(node_info)
    
    return context


def format_result(result) -> str:
    """Extract readable text from a search result"""
    import re
    
    # Try different ways to extract text from the result
    text = None
    
    if hasattr(result, 'text'):
        text = result.text
    elif hasattr(result, 'answer'):
        text = result.answer
    elif hasattr(result, 'content'):
        text = result.content
    elif hasattr(result, 'description') and result.description:
        text = result.description
    elif hasattr(result, 'name') and hasattr(result, 'type'):
        # This looks like an Entity object - extract meaningful info
        parts = []
        if hasattr(result, 'name') and result.name:
            parts.append(f"Entity: {result.name}")
        if hasattr(result, 'description') and result.description:
            # Keep original formatting of description
            parts.append(f"\n{result.description}")
        if hasattr(result, 'type') and result.type:
            parts.append(f"Type: {result.type}")
        return "\n".join(parts) if parts else str(result)
    elif isinstance(result, dict):
        # Try common keys
        for key in ['text', 'answer', 'content', 'response', 'output', 'description']:
            if key in result and result[key]:
                text = str(result[key])
                break
        # Try to extract name and description from dict
        if not text and 'name' in result:
            parts = [f"Entity: {result['name']}"]
            if 'description' in result and result['description']:
                parts.append(f"\n{result['description']}")
            if 'type' in result:
                parts.append(f"Type: {result['type']}")
            return "\n".join(parts)
    elif isinstance(result, str):
        text = result
    
    # If we found text, return it preserving original formatting
    if text:
        return text
    
    # Last resort: try to extract readable parts from string representation
    result_str = str(result)
    
    # Try to extract 'name' and 'description' from object string
    name_match = re.search(r"'name':\s*'([^']+)'", result_str)
    desc_match = re.search(r"'description':\s*'([^']+)'", result_str)
    
    if name_match or desc_match:
        parts = []
        if name_match:
            parts.append(f"Entity: {name_match.group(1)}")
        if desc_match:
            desc = desc_match.group(1)
            # Unescape newlines and special characters
            desc = desc.replace('\\n', '\n').replace('\\t', '\t')
            parts.append(f"\n{desc}")
        return "\n".join(parts)
    
    # If it's too long and looks like an object, just show a summary
    if len(result_str) > 200:
        return f"[Complex result object - {len(result_str)} characters]"
    
    return result_str


def display_history_table(history: ConversationHistory):
    """Display conversation history in a Rich table"""
    if not history.history:
        console.print("[yellow]No conversation history yet.[/yellow]")
        return
    
    table = Table(
        title="Conversation History",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta"
    )
    
    table.add_column("#", style="cyan", width=4)
    table.add_column("Question & Answer", style="white", width=60)
    table.add_column("Context Info", style="green", width=30)
    
    for entry in history.history:
        # Truncate question and answer for table display
        question = entry['question'][:100] + "..." if len(entry['question']) > 100 else entry['question']
        answer = entry['answer'][:200] + "..." if len(entry['answer']) > 200 else entry['answer']
        
        qa_text = f"[bold]Q:[/bold] {question}\n[bold]A:[/bold] {answer}"
        
        # Context summary
        node_count = len(entry['context'].get('graph_nodes', []))
        context_text = f"🔍 [cyan]show context {entry['number']}[/cyan]\n{node_count} graph nodes used\n{entry['timestamp']}"
        
        table.add_row(
            str(entry['number']),
            qa_text,
            context_text
        )
    
    console.print(table)
    console.print(f"\n[dim]Type 'show context N' to see full context for question #N[/dim]")


def display_context_detail(entry: Dict[str, Any]):
    """Display detailed context for a specific Q&A entry"""
    if not entry:
        console.print("[red]Entry not found.[/red]")
        return
    
    # Create panel with context details
    context = entry['context']
    
    content = []
    content.append(f"[bold]Original Query:[/bold] {entry['question']}")
    content.append(f"[bold]Search Type:[/bold] GRAPH_COMPLETION")
    content.append(f"[bold]Timestamp:[/bold] {entry['timestamp']}")
    content.append("")
    content.append("[bold cyan]Full LLM Prompt:[/bold cyan]")
    content.append("")
    
    # Display the actual prompt sent to the LLM if captured
    llm_prompt = context.get('llm_prompt')
    if llm_prompt and 'messages' in llm_prompt:
        content.append(f"[bold]Model:[/bold] {llm_prompt.get('model', 'Unknown')}")
        content.append("")
        
        for i, message in enumerate(llm_prompt['messages'], 1):
            role = message.get('role', 'unknown')
            msg_content = message.get('content', '')
            
            content.append(f"[bold yellow]Message #{i} ({role.upper()}):[/bold yellow]")
            
            # Handle multi-line content
            if '\n' in str(msg_content):
                lines = str(msg_content).split('\n')
                # Show all lines for system and user messages
                for line in lines[:50]:  # Limit to 50 lines
                    content.append(f"  {line}")
                if len(lines) > 50:
                    content.append(f"  ... [+{len(lines) - 50} more lines]")
            else:
                # Show full content for short messages
                if len(str(msg_content)) > 1000:
                    content.append(f"  {str(msg_content)[:1000]}...")
                else:
                    content.append(f"  {msg_content}")
            content.append("")
    else:
        content.append("[yellow]LLM prompt not captured (callback may not be working)[/yellow]")
        content.append("")
        content.append("[bold cyan]Graph Results Retrieved:[/bold cyan]")
        content.append("")
    
    # Display graph nodes with ALL extracted information
    graph_nodes = context.get('graph_nodes', [])
    if graph_nodes:
        for i, node in enumerate(graph_nodes, 1):
            content.append(f"[green]━━━ Result #{i} ({node.get('raw_type', 'Unknown')}) ━━━[/green]")
            
            # Display all attributes we captured
            for key, value in node.items():
                if key not in ['index', 'raw_type']:
                    # Format the key nicely
                    display_key = key.replace('_', ' ').title()
                    content.append(f"  [bold]{display_key}:[/bold]")
                    
                    # Handle multi-line values
                    if '\n' in str(value):
                        for line in str(value).split('\n')[:10]:  # Limit to 10 lines
                            content.append(f"    {line}")
                        if str(value).count('\n') > 10:
                            content.append(f"    ... [+{str(value).count('\n') - 10} more lines]")
                    else:
                        # Truncate very long single-line values
                        str_val = str(value)
                        if len(str_val) > 500:
                            content.append(f"    {str_val[:500]}...")
                        else:
                            content.append(f"    {str_val}")
            content.append("")
    else:
        content.append("[yellow]No graph node information extracted.[/yellow]")
        content.append("")
    
    # Show raw results for complete transparency
    content.append("[bold yellow]Raw Results (for debugging):[/bold yellow]")
    content.append("")
    for i, raw in enumerate(context.get('raw_results', []), 1):
        content.append(f"[dim]Raw Result #{i}:[/dim]")
        # Show first 300 chars of raw result
        raw_preview = raw[:300] + "..." if len(raw) > 300 else raw
        content.append(f"[dim]{raw_preview}[/dim]")
        content.append("")
    
    content.append(f"[bold]Total Results:[/bold] {context.get('total_results', 0)}")
    
    panel = Panel(
        "\n".join(content),
        title=f"[bold]Context for Question #{entry['number']}[/bold]",
        border_style="cyan",
        box=box.DOUBLE
    )
    
    console.print(panel)


def parse_command(user_input: str, history: ConversationHistory) -> bool:
    """Parse and execute special commands. Returns True if command was handled."""
    user_input = user_input.strip().lower()
    
    if user_input in ['quit', 'exit', 'q']:
        return True
    
    if user_input == 'show history':
        display_history_table(history)
        return True
    
    if user_input.startswith('show context '):
        try:
            number = int(user_input.split()[-1])
            entry = history.get_entry(number)
            display_context_detail(entry)
        except (ValueError, IndexError):
            console.print("[red]Invalid command. Use: show context N (where N is a number)[/red]")
        return True
    
    if user_input == 'clear':
        history.clear()
        console.print("[green]✓ Conversation history cleared.[/green]")
        return True
    
    if user_input.startswith('export '):
        try:
            filepath = user_input.split(maxsplit=1)[1]
            history.export_to_json(filepath)
            console.print(f"[green]✓ History exported to {filepath}[/green]")
        except IndexError:
            console.print("[red]Usage: export <filepath>[/red]")
        return True
    
    if user_input == 'help':
        help_text = """
[bold cyan]Available Commands:[/bold cyan]

[green]show history[/green]     - Display conversation history table
[green]show context N[/green]   - Show full context for question #N
[green]clear[/green]            - Clear conversation history
[green]export <file>[/green]   - Export history to JSON file
[green]help[/green]             - Show this help message
[green]quit/exit/q[/green]     - Exit the program

[dim]Just type your question to search the knowledge graph![/dim]
        """
        console.print(Panel(help_text, title="Help", border_style="blue"))
        return True
    
    return False


async def main():
    """Main function to analyze PDF with cognee"""
    
    console.print("[bold green]🔧 Cognee configured with Claude Sonnet 4.5 + FastEmbed[/bold green]")
    
    # Find PDF files
    input_dir = Path("input")
    pdf_files = list(input_dir.glob("*.pdf"))
    
    if not pdf_files:
        console.print("[red]❌ No PDF files found in input/ directory[/red]")
        return
    
    # Load processed files record
    processed_files = load_processed_files()
    
    # Check which files need processing
    new_files = []
    for pdf_path in pdf_files:
        file_hash = get_file_hash(pdf_path)
        file_name = pdf_path.name
        
        if file_name not in processed_files or processed_files[file_name] != file_hash:
            new_files.append((pdf_path, file_hash))
            console.print(f"[yellow]📄 New/modified file: {file_name}[/yellow]")
        else:
            console.print(f"[green]✓ Already processed: {file_name}[/green]")
    
    if not new_files:
        console.print("\n[green]✅ All files already processed! Skipping to query mode...[/green]")
    else:
        console.print(f"\n[cyan]📥 Processing {len(new_files)} new/modified file(s)...[/cyan]")
        
        # Add and process new files
        for pdf_path, file_hash in new_files:
            console.print(f"\n[cyan]📥 Adding document to cognee: {pdf_path.name}[/cyan]")
            await cognee.add(str(pdf_path))
            
            # Update processed files record
            processed_files[pdf_path.name] = file_hash
        
        # Process all new documents (this creates the knowledge graph)
        console.print("\n[bold cyan]🧠 Processing documents with cognee...[/bold cyan]")
        console.print("This will:")
        console.print("  - Extract text from PDFs")
        console.print("  - Chunk the content")
        console.print("  - Create embeddings")
        console.print("  - Build knowledge graph")
        console.print("  - Store in vector database")
        await cognee.cognify()
        
        # Enrich the knowledge graph with derived facts
        console.print("\n[bold cyan]✨ Enriching knowledge graph with memify...[/bold cyan]")
        console.print("This will:")
        console.print("  - Extract derived facts from existing graph")
        console.print("  - Create new associations and relationships")
        console.print("  - Add coding rules and semantic understanding")
        console.print("  - Enhance searchability with richer context")
        await cognee.memify()
        
        # Save the updated processed files record
        save_processed_files(processed_files)
    
    console.print("\n[bold green]✅ Document processed successfully![/bold green]")
    
    # Initialize conversation history
    history = ConversationHistory()
    
    # Interactive query mode
    console.print("\n" + "="*70)
    console.print("[bold magenta]💬 INTERACTIVE QUERY MODE[/bold magenta]")
    console.print("="*70)
    console.print("You can now ask questions about the document.")
    console.print("[dim]Type 'help' for available commands or 'quit' to exit.[/dim]\n")
    
    while True:
        try:
            user_query = console.input("[bold cyan]Your question:[/bold cyan] ").strip()
            
            if not user_query:
                continue
            
            # Check for special commands
            if parse_command(user_query, history):
                if user_query.lower() in ['quit', 'exit', 'q']:
                    console.print("\n[bold green]👋 Goodbye![/bold green]")
                    break
                continue
            
            # Regular search query
            console.print("\n[yellow]🔍 Searching...[/yellow]")
            
            # Reset the global prompt capture
            global last_llm_prompt
            last_llm_prompt = None
            
            # Use GRAPH_COMPLETION for better conversational answers
            results = await cognee.search(user_query, cognee.SearchType.GRAPH_COMPLETION)
            
            if not results:
                console.print("\n[red]❌ No results found. Try rephrasing your question.[/red]\n")
                continue
            
            # Extract context information
            context = extract_context_info(results)
            context['query'] = user_query
            context['search_type'] = 'GRAPH_COMPLETION'
            
            # Capture the LLM prompt if available
            if last_llm_prompt:
                context['llm_prompt'] = last_llm_prompt
            
            # Format answer
            console.print(f"\n[bold green]💡 Answer:[/bold green]\n")
            console.print("=" * 70)
            
            # Deduplicate results based on formatted text
            seen_texts = set()
            formatted_parts = []
            for result in results:
                text = format_result(result)
                if text not in seen_texts:
                    seen_texts.add(text)
                    formatted_parts.append(text)
            
            answer = "\n\n".join(formatted_parts)
            console.print(answer)
            console.print()
            console.print("=" * 70)
            
            # Add to conversation history
            history.add_entry(user_query, answer, context)
            
            # Show quick hint about context
            console.print(f"\n[dim]💡 Type 'show context {len(history.history)}' to see what graph information was used[/dim]")
            console.print(f"[dim]💡 Type 'show history' to see all Q&A history[/dim]\n")
            
        except KeyboardInterrupt:
            console.print("\n\n[bold green]👋 Goodbye![/bold green]")
            break
        except Exception as e:
            console.print(f"\n[red]❌ Error: {e}[/red]")


if __name__ == "__main__":
    asyncio.run(main())
