"""polyphony codebook management commands."""

from __future__ import annotations

import csv
import json
import os
import shlex
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
@click.option("--skip-referee", is_flag=True, default=False,
              help="Skip the referee deduplication pass")
@click.pass_context
def induce(ctx, sample_size, agent, human_leads, skip_referee):
    """
    Generate an initial codebook inductively from your data.

    Each agent independently reads a random sample of segments and proposes
    candidate codes. A referee model then reviews the merged list for
    near-duplicates and overlaps before you do the final review.

    \b
    Examples:
        polyphony codebook induce                    # sample 20 segments
        polyphony codebook induce --sample-size 50   # larger sample
        polyphony codebook induce --human-leads      # human proposes codes first
        polyphony codebook induce --skip-referee     # skip dedup pass
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    agent_a, agent_b, supervisor = build_agent_objects(conn, project["id"])

    if human_leads and supervisor is None:
        console.print(
            "[yellow]Warning: --human-leads requires a supervisor agent. "
            "Falling back to LLM-only induction.[/]"
        )
        human_leads = False

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
        skip_referee=skip_referee,
    )
    conn.close()


@codebook.command("import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--finalize", is_flag=True, default=False,
              help="Mark the imported codebook as final immediately")
@click.pass_context
def import_codebook(ctx, file, finalize):
    """
    Import a pre-existing codebook from a YAML, JSON, or CSV file.

    This supports deductive (theory-driven) coding workflows where the
    codebook is defined before data analysis begins.

    The YAML/JSON format should contain a 'codes' list, matching the
    format produced by `polyphony export codebook`.

    \b
    Examples:
        polyphony codebook import codebook.yaml
        polyphony codebook import codebook.json --finalize
        polyphony codebook import codebook.csv
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    file_path = Path(file)
    suffix = file_path.suffix.lower()

    # Parse the file
    try:
        if suffix in (".yaml", ".yml"):
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            codes = data.get("codes", data) if isinstance(data, dict) else data
        elif suffix == ".json":
            data = json.loads(file_path.read_text(encoding="utf-8"))
            codes = data.get("codes", data) if isinstance(data, dict) else data
        elif suffix == ".csv":
            with file_path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                codes = list(reader)
        else:
            console.print(f"[red]Unsupported file format: {suffix}[/]")
            console.print("Supported formats: .yaml, .yml, .json, .csv")
            sys.exit(1)
    except Exception as e:
        console.print(f"[red]Failed to parse {file_path.name}: {e}[/]")
        sys.exit(1)

    if not isinstance(codes, list) or len(codes) == 0:
        console.print("[red]No codes found in the file.[/]")
        sys.exit(1)

    # Validate and normalise codes
    validated = []
    for i, code in enumerate(codes):
        name = code.get("name", "").strip()
        if not name:
            console.print(f"[red]Code at index {i} has no name — skipping.[/]")
            continue
        validated.append({
            "name": name.upper(),
            "description": code.get("description", ""),
            "level": code.get("level", "open"),
            "inclusion_criteria": code.get("inclusion_criteria") or None,
            "exclusion_criteria": code.get("exclusion_criteria") or None,
            "example_quotes": code.get("example_quotes", []),
        })

    if not validated:
        console.print("[red]No valid codes found after parsing.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    # Find next version number
    existing = fetchone(
        conn,
        "SELECT MAX(version) as v FROM codebook_version WHERE project_id = ?",
        (project["id"],),
    )
    next_version = (existing["v"] or 0) + 1

    from ..pipeline.induction import save_codebook_version

    stage = "final" if finalize else "imported"
    rationale = f"Imported from {file_path.name}"

    cb_id = save_codebook_version(
        conn,
        project_id=project["id"],
        codes=validated,
        version=next_version,
        stage=stage,
        rationale=rationale,
    )

    conn.close()

    console.print(
        f"[green]Imported {len(validated)} codes as codebook v{next_version} "
        f"(stage={stage}).[/]"
    )


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
    subprocess.run(shlex.split(editor) + [tmp_path], check=True)

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
