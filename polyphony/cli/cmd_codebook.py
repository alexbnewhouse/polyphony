"""polyphony codebook management commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.tree import Tree

from ..db import connect, fetchall, fetchone, insert, json_col, update
from ..utils import build_agent_objects, get_active_codebook

console = Console()


@click.group()
def codebook():
    """Design and manage the coding scheme."""


@codebook.command("induce")
@click.option("--sample-size", "-n", default=20, show_default=True,
              help="Number of segments to sample for induction")
@click.option("--agent", type=click.Choice(["a", "b", "both"]), default="both",
              show_default=True, help="Which agent(s) run induction")
@click.option("--human-leads", is_flag=True, default=False,
              help="Human proposes codes first, then sees LLM suggestions")
@click.pass_context
def induce(ctx, sample_size, agent, human_leads):
    """
    Generate an initial codebook inductively from your data.

    Each agent independently reads a random sample of segments and proposes
    candidate codes. You then review the merged suggestions and approve,
    edit, merge, or reject individual codes — building the working codebook
    interactively before full coding begins.

    With --human-leads, you propose codes first from the sample, then
    optionally see LLM suggestions merged in.

    \b
    Examples:
        polyphony codebook induce                    # sample 20 segments
        polyphony codebook induce --sample-size 50   # larger sample
        polyphony codebook induce --human-leads      # human proposes codes first
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    agent_a, agent_b, supervisor = build_agent_objects(conn, project["id"])

    from ..pipeline.induction import run_induction

    run_induction(
        conn=conn,
        project=project,
        agent_a=agent_a,
        agent_b=agent_b,
        sample_size=sample_size,
        skip_agent_b=(agent == "a"),
        human_leads=human_leads,
        supervisor_agent=supervisor,
    )
    conn.close()


@codebook.command("show")
@click.option("--version", "-v", default=None, type=int, help="Codebook version (default: latest)")
@click.pass_context
def show(ctx, version):
    """Display the codebook as a structured tree."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    if version:
        cb = fetchone(conn, "SELECT * FROM codebook_version WHERE project_id = ? AND version = ?",
                      (project["id"], version))
    else:
        cb = get_active_codebook(conn, project["id"])

    if not cb:
        console.print("[yellow]No codebook yet. Run `polyphony codebook induce` first.[/]")
        conn.close()
        return

    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? ORDER BY level, sort_order, name",
        (cb["id"],),
    )
    conn.close()

    tree = Tree(
        f"[bold cyan]Codebook v{cb['version']}[/] "
        f"({cb['stage']}) — {len(codes)} codes"
    )

    by_level = {"open": [], "axial": [], "selective": []}
    for c in codes:
        if c["is_active"]:
            by_level.setdefault(c["level"], []).append(c)

    for level, label, color in [
        ("open", "Open Codes", "green"),
        ("axial", "Axial Codes", "yellow"),
        ("selective", "Selective Codes", "red"),
    ]:
        if by_level[level]:
            branch = tree.add(f"[bold {color}]{label}[/]")
            for c in by_level[level]:
                node = branch.add(f"[bold]{c['name']}[/]")
                node.add(c["description"])
                if c.get("inclusion_criteria"):
                    node.add(f"[dim]Include: {c['inclusion_criteria']}[/]")
                if c.get("exclusion_criteria"):
                    node.add(f"[dim]Exclude: {c['exclusion_criteria']}[/]")

    console.print(tree)


@codebook.command("add")
@click.option("--name", "-n", required=True, help="Code name (UPPER_CASE recommended)")
@click.option("--description", "-d", required=True, help="What this code means")
@click.option("--level", "-l",
              type=click.Choice(["open", "axial", "selective"]),
              default="open", show_default=True)
@click.option("--include", default="", help="Inclusion criteria")
@click.option("--exclude", default="", help="Exclusion criteria")
@click.pass_context
def add_code(ctx, name, description, level, include, exclude):
    """Add a new code to the current codebook version."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    cb = get_active_codebook(conn, project["id"])
    if not cb:
        console.print("[red]No codebook. Run `polyphony codebook induce` first.[/]")
        conn.close()
        return

    insert(conn, "code", {
        "project_id": project["id"],
        "codebook_version_id": cb["id"],
        "name": name.upper(),
        "description": description,
        "level": level,
        "inclusion_criteria": include or None,
        "exclusion_criteria": exclude or None,
        "example_quotes": "[]",
        "is_active": 1,
        "sort_order": 999,
    })
    conn.commit()
    conn.close()
    console.print(f"[green]Code '{name.upper()}' added to v{cb['version']}.[/]")


@codebook.command("edit")
@click.argument("code_name")
@click.pass_context
def edit_code(ctx, code_name):
    """
    Edit a code definition in your $EDITOR.

    Opens a YAML file with the code's fields. Save and close to update.
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    cb = get_active_codebook(conn, project["id"])
    if not cb:
        console.print("[red]No codebook.[/]")
        conn.close()
        return

    code = fetchone(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? AND name = ? COLLATE NOCASE",
        (cb["id"], code_name),
    )
    if not code:
        console.print(f"[red]Code '{code_name}' not found.[/]")
        conn.close()
        return

    # Build editable YAML
    editable = {
        "name": code["name"],
        "description": code["description"],
        "level": code["level"],
        "inclusion_criteria": code["inclusion_criteria"] or "",
        "exclusion_criteria": code["exclusion_criteria"] or "",
        "example_quotes": json.loads(code["example_quotes"] or "[]"),
    }

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write("# Edit this code definition. Save and close when done.\n")
        yaml.dump(editable, f, allow_unicode=True, default_flow_style=False)
        tmp_path = f.name

    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, tmp_path], check=True)

    updated = yaml.safe_load(Path(tmp_path).read_text())
    Path(tmp_path).unlink()

    conn.execute(
        """UPDATE code SET
           name = ?, description = ?, level = ?,
           inclusion_criteria = ?, exclusion_criteria = ?,
           example_quotes = ?
           WHERE id = ?""",
        (
            updated["name"],
            updated["description"],
            updated["level"],
            updated.get("inclusion_criteria") or None,
            updated.get("exclusion_criteria") or None,
            json.dumps(updated.get("example_quotes", [])),
            code["id"],
        ),
    )
    conn.commit()
    conn.close()
    console.print(f"[green]Code '{updated['name']}' updated.[/]")


@codebook.command("finalize")
@click.option("--notes", default="", help="Notes about this final version")
@click.pass_context
def finalize(ctx, notes):
    """Mark the current codebook version as final."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    cb = get_active_codebook(conn, project["id"])
    if not cb:
        console.print("[red]No codebook.[/]")
        conn.close()
        return

    if not Confirm.ask(f"Finalize codebook v{cb['version']}?"):
        console.print("[dim]Cancelled.[/]")
        conn.close()
        return

    conn.execute(
        "UPDATE codebook_version SET stage = 'final', rationale = ? WHERE id = ?",
        (notes or "Finalized by supervisor", cb["id"]),
    )
    conn.commit()
    conn.close()
    console.print(f"[green]Codebook v{cb['version']} marked as final.[/]")


@codebook.command("history")
@click.pass_context
def history(ctx):
    """Show all codebook versions."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    versions = fetchall(
        conn,
        "SELECT v.*, COUNT(c.id) AS code_count FROM codebook_version v "
        "LEFT JOIN code c ON c.codebook_version_id = v.id AND c.is_active = 1 "
        "WHERE v.project_id = ? GROUP BY v.id ORDER BY v.version",
        (project["id"],),
    )
    conn.close()

    from rich.table import Table
    table = Table(title="Codebook History")
    table.add_column("Version", justify="right")
    table.add_column("Stage")
    table.add_column("Codes", justify="right")
    table.add_column("Rationale")
    table.add_column("Created")
    for v in versions:
        table.add_row(
            str(v["version"]), v["stage"], str(v["code_count"]),
            (v["rationale"] or "")[:50], (v["created_at"] or "")[:10]
        )
    console.print(table)
