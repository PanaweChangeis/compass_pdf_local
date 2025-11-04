"""
Multi-Modal PDF Analysis with Cognee
=====================================

This script demonstrates loading documents using Textract JSON and page images
instead of processing PDFs directly. This approach is:
- Faster (no PDF parsing)
- Richer (preserves Textract metadata)
- Multi-modal (text + visual context)

Usage:
    python analyze_multimodal.py --bucket BUCKET --document-id DOC_ID
"""

import asyncio
import argparse
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment before importing cognee
load_dotenv()

import cognee
from textract_loader import load_document_for_cognee
from rich.console import Console
from rich.panel import Panel

console = Console()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def load_and_process_document(
    s3_bucket: str,
    document_id: str,
    use_images: bool = True
):
    """
    Load and process a document using Textract data and page images.
    
    Args:
        s3_bucket: S3 bucket containing processed documents
        document_id: Document ID (used as S3 prefix)
        use_images: Whether to load page images for visual context
    """
    console.print(Panel(
        f"[bold cyan]Loading Document: {document_id}[/bold cyan]\n"
        f"Bucket: {s3_bucket}\n"
        f"Multi-modal: {'Yes (Text + Images)' if use_images else 'No (Text only)'}",
        title="Multi-Modal Document Loading"
    ))
    
    # Construct S3 paths
    textract_key = f"{document_id}/textract_output_blocks.json"
    page_images_prefix = f"{document_id}/pages/" if use_images else None
    
    # Load document data
    console.print("\n[yellow]📥 Loading Textract data and page images...[/yellow]")
    
    structured_text, image_paths = load_document_for_cognee(
        textract_s3_bucket=s3_bucket,
        textract_s3_key=textract_key,
        page_images_s3_bucket=s3_bucket if use_images else None,
        page_images_s3_prefix=page_images_prefix,
        local_images_dir=Path(f"temp_images/{document_id}")
    )
    
    console.print(f"[green]✓ Loaded structured text ({len(structured_text)} characters)[/green]")
    console.print(f"[green]✓ Downloaded {len(image_paths)} page images[/green]")
    
    # Save structured text to temporary file
    text_file = Path(f"temp_text/{document_id}.md")
    text_file.parent.mkdir(parents=True, exist_ok=True)
    text_file.write_text(structured_text)
    
    console.print(f"\n[cyan]📝 Saved structured text to: {text_file}[/cyan]")
    
    # Add text to Cognee
    console.print("\n[yellow]🧠 Adding text to Cognee...[/yellow]")
    await cognee.add(str(text_file), data_type="text")
    console.print("[green]✓ Text added to Cognee[/green]")
    
    # Add images to Cognee
    if image_paths:
        console.print(f"\n[yellow]🖼️  Adding {len(image_paths)} page images to Cognee...[/yellow]")
        for i, image_path in enumerate(image_paths, 1):
            console.print(f"  Adding page {i}/{len(image_paths)}: {image_path.name}")
            await cognee.add(str(image_path))
        console.print("[green]✓ All images added to Cognee[/green]")
    
    # Process with Cognee
    console.print("\n[bold yellow]🔄 Processing with Cognee (cognify)...[/bold yellow]")
    console.print("This will:")
    console.print("  - Extract entities and relationships")
    console.print("  - Create embeddings")
    console.print("  - Build knowledge graph")
    console.print("  - Analyze visual content (if images provided)")
    
    await cognee.cognify()
    console.print("[green]✓ Document processed successfully![/green]")
    
    # Enrich with memify
    console.print("\n[bold yellow]✨ Enriching knowledge graph (memify)...[/bold yellow]")
    await cognee.memify()
    console.print("[green]✓ Knowledge graph enriched![/green]")
    
    console.print(Panel(
        "[bold green]✅ Multi-modal document loading complete![/bold green]\n\n"
        "You can now query the document with both text and visual understanding.",
        title="Success"
    ))


async def interactive_query():
    """Interactive query mode."""
    console.print("\n" + "="*70)
    console.print("[bold magenta]💬 INTERACTIVE QUERY MODE[/bold magenta]")
    console.print("="*70)
    console.print("Ask questions about the document.")
    console.print("[dim]Type 'quit' to exit.[/dim]\n")
    
    while True:
        try:
            query = console.input("[bold cyan]Your question:[/bold cyan] ").strip()
            
            if not query:
                continue
            
            if query.lower() in ['quit', 'exit', 'q']:
                console.print("\n[bold green]👋 Goodbye![/bold green]")
                break
            
            console.print("\n[yellow]🔍 Searching...[/yellow]")
            
            # Use GRAPH_COMPLETION for conversational answers
            results = await cognee.search(query, cognee.SearchType.GRAPH_COMPLETION)
            
            if not results:
                console.print("\n[red]❌ No results found.[/red]\n")
                continue
            
            # Display results
            console.print(f"\n[bold green]💡 Answer:[/bold green]\n")
            console.print("=" * 70)
            
            for result in results:
                if hasattr(result, 'text'):
                    console.print(result.text)
                elif hasattr(result, 'answer'):
                    console.print(result.answer)
                elif isinstance(result, str):
                    console.print(result)
                else:
                    console.print(str(result))
            
            console.print()
            console.print("=" * 70)
            console.print()
            
        except KeyboardInterrupt:
            console.print("\n\n[bold green]👋 Goodbye![/bold green]")
            break
        except Exception as e:
            console.print(f"\n[red]❌ Error: {e}[/red]\n")


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Multi-modal PDF analysis using Textract and page images"
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="S3 bucket containing processed documents"
    )
    parser.add_argument(
        "--document-id",
        required=True,
        help="Document ID (S3 prefix)"
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Load text only (skip page images)"
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Skip interactive query mode"
    )
    
    args = parser.parse_args()
    
    # Load and process document
    await load_and_process_document(
        s3_bucket=args.bucket,
        document_id=args.document_id,
        use_images=not args.text_only
    )
    
    # Interactive query mode
    if not args.no_interactive:
        await interactive_query()


if __name__ == "__main__":
    asyncio.run(main())
