"""polyphony data import commands."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..db import connect, fetchall, fetchone
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

    Supported formats: .txt, .md, .docx, .csv, .json

    Each document is split into segments using the --segment-by strategy:
      paragraph  — split on blank lines (good for interview transcripts)
      sentence   — groups of ~3 sentences (good for survey responses)
      fixed:<n>  — fixed word-count windows, e.g. fixed:150
      manual     — one segment per document (good for pre-segmented data)

    \b
    Examples:
        polyphony data import interviews/*.txt --segment-by paragraph
        polyphony data import survey.csv --segment-by sentence --content-col response
        polyphony data import transcript.docx --segment-by fixed:150
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project. Run `polyphony project new` first.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    result = import_documents(
        conn=conn,
        project_id=project["id"],
        paths=[Path(f) for f in files],
        segment_strategy=segment_by,
        content_col=content_col,
        min_segment_length=min_length,
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
        "SELECT id, filename, word_count, status, imported_at FROM document WHERE project_id = ?",
        (project["id"],),
    )
    conn.close()

    if not docs:
        console.print("[dim]No documents imported yet.[/]")
        return

    table = Table(title="Documents", show_header=True)
    table.add_column("ID", width=5)
    table.add_column("Filename")
    table.add_column("Words", justify="right")
    table.add_column("Status")
    table.add_column("Imported")
    for d in docs:
        table.add_row(
            str(d["id"]), d["filename"], str(d["word_count"]),
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
            "SELECT segment_index, text, is_calibration FROM segment WHERE document_id = ? ORDER BY segment_index",
            (doc_id,),
        )
        from rich.panel import Panel
        for s in segs:
            cal_tag = " [yellow][CAL][/]" if s["is_calibration"] else ""
            console.print(Panel(
                s["text"],
                title=f"[cyan]Segment {s['segment_index']}{cal_tag}[/]",
                border_style="dim",
            ))
    else:
        console.print(doc["content"])

    conn.close()
