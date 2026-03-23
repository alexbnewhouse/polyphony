"""polyphony data import commands."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..db import connect, fetchall, fetchone
from ..generators import generate_llm_data, generate_template_data, get_domains
from ..io.fetchers import fetch_images_from_csv
from ..io.importers import import_documents

console = Console()


@click.group()
def data():
    """Import and inspect corpus documents.

    Documents are split into segments — the units that agents code.
    Each segment is a chunk of text (e.g. a paragraph, a few sentences,
    or a fixed word window) that gets assigned one or more codes.
    """


@data.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--segment-by",
    default="paragraph",
    show_default=True,
    help="Segmentation strategy: paragraph | sentence | fixed:<n_words> | manual",
)
@click.option(
    "--content-col",
    default="content",
    show_default=True,
    help="Column name for text content (CSV only)",
)
@click.option(
    "--min-length",
    default=20,
    show_default=True,
    help="Minimum segment length in characters",
)
@click.pass_context
def import_cmd(ctx, files, segment_by, content_col, min_length):
    """
    Import documents into the active project.

    Supported formats: .txt, .md, .docx, .csv, .json,
    .png, .jpg, .jpeg, .gif, .webp, .bmp, .tiff

    Text documents are split into segments using the --segment-by strategy:
      paragraph  — split on blank lines (good for interview transcripts)
      sentence   — groups of ~3 sentences (good for survey responses)
      fixed:<n>  — fixed word-count windows, e.g. fixed:150
      manual     — one segment per document (good for pre-segmented data)

    Image files are always imported as one segment per image (no text
    segmentation). Use a vision-capable model (e.g. llava, llama3.2-vision)
    to code image segments.

    \b
    Examples:
        polyphony data import interviews/*.txt --segment-by paragraph
        polyphony data import survey.csv --segment-by sentence --content-col response
        polyphony data import transcript.docx --segment-by fixed:150
        polyphony data import photos/*.jpg
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project. Run `polyphony project new` first.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    # Derive project directory for image storage
    project_dir = db_path.parent if db_path else None

    result = import_documents(
        conn=conn,
        project_id=project["id"],
        paths=[Path(f) for f in files],
        segment_strategy=segment_by,
        content_col=content_col,
        min_segment_length=min_length,
        project_dir=project_dir,
    )
    conn.close()

    console.print(
        f"\n[green]Import complete:[/] "
        f"{result['documents_imported']} document(s), "
        f"{result['segments_created']} segment(s)."
    )
    if result["skipped"]:
        console.print(f"[yellow]Skipped {len(result['skipped'])} file(s).[/]")


@data.command("list")
@click.pass_context
def list_docs(ctx):
    """List imported documents."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    docs = fetchall(
        conn,
        "SELECT id, filename, word_count, status, imported_at, media_type FROM document WHERE project_id = ?",
        (project["id"],),
    )
    conn.close()

    if not docs:
        console.print("[dim]No documents imported yet.[/]")
        return

    table = Table(title="Documents", show_header=True)
    table.add_column("ID", width=5)
    table.add_column("Filename")
    table.add_column("Type", width=6)
    table.add_column("Words", justify="right")
    table.add_column("Status")
    table.add_column("Imported")
    for d in docs:
        media = d.get("media_type", "text")
        table.add_row(
            str(d["id"]), d["filename"], media,
            str(d["word_count"]) if media == "text" else "-",
            d["status"], (d["imported_at"] or "")[:10]
        )
    console.print(table)


@data.command("show")
@click.argument("doc_id", type=int)
@click.option("--segments", is_flag=True, help="Show segments instead of full text")
@click.pass_context
def show_doc(ctx, doc_id, segments):
    """Show a document's text or its segments."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    doc = fetchone(conn, "SELECT * FROM document WHERE id = ?", (doc_id,))
    if not doc:
        console.print(f"[red]Document {doc_id} not found.[/]")
        conn.close()
        return

    if segments:
        segs = fetchall(
            conn,
            "SELECT segment_index, text, is_calibration, media_type, image_path FROM segment WHERE document_id = ? ORDER BY segment_index",
            (doc_id,),
        )
        from rich.panel import Panel
        for s in segs:
            cal_tag = " [yellow][CAL][/]" if s["is_calibration"] else ""
            if s.get("media_type") == "image":
                console.print(Panel(
                    f"[Image: {s.get('image_path', 'unknown')}]",
                    title=f"[cyan]Segment {s['segment_index']}{cal_tag} (image)[/]",
                    border_style="dim",
                ))
            else:
                console.print(Panel(
                    s["text"],
                    title=f"[cyan]Segment {s['segment_index']}{cal_tag}[/]",
                    border_style="dim",
                ))
    else:
        if doc.get("media_type") == "image":
            console.print(f"[Image document: {doc.get('image_path', doc['filename'])}]")
        else:
            console.print(doc["content"])

    conn.close()


@data.command("fetch-images")
@click.argument("csv_path", type=click.Path(exists=True))
@click.option(
    "--url-column",
    default="url",
    show_default=True,
    help="CSV column containing image URLs",
)
@click.option(
    "--metadata-columns",
    default=None,
    help="Comma-separated list of CSV columns to keep as metadata (default: all non-URL columns)",
)
@click.option(
    "--timeout",
    default=30,
    show_default=True,
    help="Download timeout in seconds per image",
)
@click.option(
    "--max-concurrent",
    default=5,
    show_default=True,
    help="Maximum number of concurrent downloads",
)
@click.pass_context
def fetch_images(ctx, csv_path, url_column, metadata_columns, timeout, max_concurrent):
    """
    Fetch images from URLs in a CSV file and import them.

    Downloads images listed in CSV_PATH, saves them locally, and imports
    them into the active project. Each image becomes one document with
    one segment (manual segmentation).

    \b
    Examples:
        polyphony data fetch-images urls.csv
        polyphony data fetch-images urls.csv --url-column image_url
        polyphony data fetch-images urls.csv --metadata-columns "label,source" --max-concurrent 10
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project. Run `polyphony project new` first.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    project_dir = db_path.parent if db_path else None

    if project_dir is None:
        console.print("[red]Cannot determine project directory for image storage.[/]")
        conn.close()
        sys.exit(1)

    images_dir = Path(project_dir) / "images"

    # Parse metadata columns if provided
    meta_cols = None
    if metadata_columns:
        meta_cols = [c.strip() for c in metadata_columns.split(",")]

    # Fetch images from CSV
    console.print(f"[bold]Fetching images from[/] {csv_path}")
    fetch_result = fetch_images_from_csv(
        csv_path=Path(csv_path),
        images_dir=images_dir,
        url_column=url_column,
        metadata_columns=meta_cols,
        timeout=timeout,
        max_concurrent=max_concurrent,
    )

    downloaded = fetch_result["downloaded"]
    skipped = fetch_result["skipped"]
    failed = fetch_result["failed"]

    console.print(
        f"\n[bold]Fetch complete:[/] "
        f"[green]{len(downloaded)} downloaded[/], "
        f"[yellow]{len(skipped)} skipped[/], "
        f"[red]{len(failed)} failed[/]"
    )

    if failed:
        for entry in failed:
            console.print(f"  [red]x[/] {entry['url']}: {entry.get('error', 'unknown error')}")

    # Import downloaded images into the project
    if downloaded:
        image_paths = [entry["path"] for entry in downloaded]
        console.print(f"\n[bold]Importing {len(image_paths)} image(s) into project...[/]")

        result = import_documents(
            conn=conn,
            project_id=project["id"],
            paths=image_paths,
            segment_strategy="manual",
            project_dir=project_dir,
        )

        console.print(
            f"\n[green]Import complete:[/] "
            f"{result['documents_imported']} document(s), "
            f"{result['segments_created']} segment(s)."
        )
        if result["skipped"]:
            console.print(f"[yellow]Skipped {len(result['skipped'])} file(s) during import.[/]")
    else:
        console.print("\n[dim]No new images to import.[/]")

    conn.close()


@data.command("generate")
@click.option("--domain", default=None, help="Pre-built domain name (e.g. housing, healthcare, education)")
@click.option("--topic", default=None, help="Custom topic for LLM-based generation (requires Ollama)")
@click.option("--model", default="llama3.2", show_default=True, help="Ollama model for custom topic generation")
@click.option("--segments", default=20, show_default=True, help="Number of segments to generate")
@click.option("--list-domains", is_flag=True, help="Show available pre-built domains and exit")
@click.option("--output", default=None, type=click.Path(), help="Export to CSV file instead of importing")
@click.option("--seed", default=None, type=int, help="Random seed for reproducibility")
@click.pass_context
def generate(ctx, domain, topic, model, segments, list_domains, output, seed):
    """Generate synthetic qualitative data for training and practice.

    Uses pre-built domains with realistic interview excerpt templates, or
    generates custom data via a local Ollama LLM.

    \b
    Examples:
        polyphony data generate --list-domains
        polyphony data generate --domain housing --segments 30
        polyphony data generate --domain healthcare --output training.csv
        polyphony data generate --topic "climate anxiety" --segments 25
    """
    # --- List domains mode ---
    if list_domains:
        domains = get_domains()
        table = Table(title="Available Domains", show_header=True)
        table.add_column("Key", style="cyan")
        table.add_column("Description")
        for key, desc in domains.items():
            table.add_row(key, desc)
        console.print(table)
        return

    # --- Validate options ---
    if domain and topic:
        console.print("[red]Cannot specify both --domain and --topic. Pick one.[/]")
        sys.exit(1)
    if not domain and not topic:
        console.print("[red]Specify --domain or --topic. Use --list-domains to see pre-built options.[/]")
        sys.exit(1)

    # --- Generate data ---
    if domain:
        console.print(f"[bold]Generating {segments} segments from domain:[/] {domain}")
        try:
            result = generate_template_data(domain=domain, n_segments=segments, seed=seed)
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            sys.exit(1)
    else:
        console.print(f"[bold]Generating {segments} segments via LLM on topic:[/] {topic}")
        try:
            result = generate_llm_data(
                topic=topic, n_segments=segments, model=model, seed=seed,
            )
        except RuntimeError as e:
            console.print(f"[red]{e}[/]")
            sys.exit(1)

    gen_segments = result["segments"]
    gen_codes = result["codes"]

    if not gen_segments:
        console.print("[yellow]No segments were generated. Try a different topic or check Ollama.[/]")
        return

    console.print(f"[green]Generated {len(gen_segments)} segment(s).[/]")

    # --- Output mode: CSV export ---
    if output:
        import csv

        out_path = Path(output)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["text", "participant", "domain", "generated"])
            writer.writeheader()
            for seg in gen_segments:
                writer.writerow(
                    {
                        "text": seg["text"],
                        "participant": seg["metadata"].get("participant", ""),
                        "domain": seg["metadata"].get("domain", ""),
                        "generated": seg["metadata"].get("generated", True),
                    }
                )
        console.print(f"[green]Exported to {out_path}[/]")

    # --- Output mode: import into project ---
    else:
        db_path = ctx.obj.get("db_path")
        if not db_path:
            console.print("[red]No active project. Run `polyphony project new` first.[/]")
            console.print("[dim]Tip: use --output to export to CSV without a project.[/]")
            sys.exit(1)

        import json
        import tempfile

        conn = connect(db_path)
        project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

        # Write segments to a temp JSON file in the format the importer expects
        json_data = [{"content": seg["text"], "metadata": seg["metadata"]} for seg in gen_segments]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        ) as tmp:
            json.dump(json_data, tmp, ensure_ascii=False, indent=2)
            temp_path = Path(tmp.name)

        try:
            import_result = import_documents(
                conn=conn,
                project_id=project["id"],
                paths=[temp_path],
                segment_strategy="manual",
            )
            console.print(
                f"[green]Import complete:[/] "
                f"{import_result['documents_imported']} document(s), "
                f"{import_result['segments_created']} segment(s)."
            )
            if import_result["skipped"]:
                console.print(f"[yellow]Skipped {len(import_result['skipped'])} file(s).[/]")
        finally:
            temp_path.unlink(missing_ok=True)
            conn.close()

    # --- Display suggested codebook ---
    if gen_codes:
        console.print("\n[bold]Suggested codebook:[/]")
        code_table = Table(show_header=True)
        code_table.add_column("Code", style="cyan")
        code_table.add_column("Description")
        code_table.add_column("Include when...")
        code_table.add_column("Exclude when...")
        for code in gen_codes:
            code_table.add_row(
                code.get("name", ""),
                code.get("description", ""),
                code.get("inclusion_criteria", ""),
                code.get("exclusion_criteria", ""),
            )
        console.print(code_table)
