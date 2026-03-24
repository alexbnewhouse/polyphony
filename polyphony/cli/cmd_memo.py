"""polyphony memo commands."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..db import connect, fetchall, fetchone, insert, json_col

console = Console()


@click.group()
def memo():
    """Write and manage analytical memos."""


@memo.command("new")
@click.option(
    "--type", "memo_type",
    type=click.Choice([
        "theoretical", "methodological", "reflexivity",
        "code_definition", "synthesis", "analytic"
    ]),
    required=True,
    help="Type of memo",
)
@click.option("--title", "-t", required=True, help="Memo title")
@click.option("--link-codes", default="", help="Comma-separated code names to link")
@click.option("--link-segments", default="", help="Comma-separated segment IDs to link")
@click.option("--tags", default="", help="Comma-separated tags")
@click.pass_context
def new_memo(ctx, memo_type, title, link_codes, link_segments, tags):
    """
    Write a new analytical memo.

    Opens your $EDITOR for the memo body. Great for capturing insights,
    theoretical ideas, and methodological decisions.

    Examples:
        polyphony memo new --type theoretical --title "Emerging theme: precarity"
        polyphony memo new --type reflexivity --title "Positionality note"
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    sup = fetchone(conn, "SELECT id FROM agent WHERE project_id = ? AND role = 'supervisor'",
                   (project["id"],))

    # Open editor for content
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(f"# {title}\n\n<!-- Write your memo here. Markdown supported. -->\n\n")
        tmp_path = f.name

    editor = os.environ.get("EDITOR", "nano")
    subprocess.run(shlex.split(editor) + [tmp_path], check=True)
    content = Path(tmp_path).read_text().strip()
    Path(tmp_path).unlink()

    if not content or content.startswith("# "):
        # Empty memo
        if not content.strip("# ").strip().replace(title, "").strip():
            console.print("[yellow]Empty memo discarded.[/]")
            conn.close()
            return

    # Resolve linked codes
    code_ids = []
    if link_codes:
        for name in [c.strip() for c in link_codes.split(",") if c.strip()]:
            code = fetchone(conn, "SELECT id FROM code WHERE name = ? COLLATE NOCASE AND project_id = ?",
                            (name, project["id"]))
            if code:
                code_ids.append(code["id"])

    # Parse segment IDs
    seg_ids = [int(x.strip()) for x in link_segments.split(",") if x.strip().isdigit()]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    memo_id = insert(conn, "memo", {
        "project_id": project["id"],
        "author_id": sup["id"] if sup else 1,
        "memo_type": memo_type,
        "title": title,
        "content": content,
        "linked_codes": json_col(code_ids),
        "linked_segments": json_col(seg_ids),
        "linked_flags": "[]",
        "tags": json_col(tag_list),
    })
    conn.commit()
    conn.close()
    console.print(f"[green]Memo #{memo_id} saved: '{title}'[/]")


@memo.command("list")
@click.option("--type", "memo_type", default=None)
@click.pass_context
def list_memos(ctx, memo_type):
    """List all memos."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    query = "SELECT m.id, m.title, m.memo_type, a.role AS author, m.created_at FROM memo m JOIN agent a ON a.id = m.author_id WHERE m.project_id = ?"
    params = [project["id"]]
    if memo_type:
        query += " AND m.memo_type = ?"
        params.append(memo_type)
    query += " ORDER BY m.created_at"
    memos = fetchall(conn, query, tuple(params))
    conn.close()

    table = Table(title="Memos")
    table.add_column("ID", width=5)
    table.add_column("Type")
    table.add_column("Title")
    table.add_column("Author")
    table.add_column("Date")
    for m in memos:
        table.add_row(str(m["id"]), m["memo_type"], m["title"],
                      m["author"], (m["created_at"] or "")[:10])
    console.print(table)


@memo.command("show")
@click.argument("memo_id", type=int)
@click.pass_context
def show_memo(ctx, memo_id):
    """Display a memo."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    m = fetchone(conn, "SELECT * FROM memo WHERE id = ?", (memo_id,))
    conn.close()

    if not m:
        console.print(f"[red]Memo {memo_id} not found.[/]")
        return

    console.print(Panel(
        m["content"],
        title=f"[bold]{m['title']}[/] ({m['memo_type']})",
    ))
