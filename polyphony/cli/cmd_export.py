"""polyphony export commands."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console

from ..db import connect, fetchone
from ..io.exporters import (
    export_assignments,
    export_codebook,
    export_llm_log,
    export_memos,
    export_replication_package,
)

console = Console()


@click.group()
def export():
    """Export project data and generate replication packages.

    \b
    Available exports:
      codebook      — the coding scheme (YAML, JSON, or CSV)
      assignments   — all segment-code assignments by agent
      memos         — analytical memos (Markdown or JSON)
      llm-log       — full LLM audit trail (JSONL, one call per line)
      replication   — complete reproducibility package for publication

    For publication, use 'polyphony export replication' to generate a directory
    with everything needed to verify or re-run the analysis.
    """


@export.command("codebook")
@click.option("--format", "fmt", type=click.Choice(["yaml", "json", "csv"]),
              default="yaml", show_default=True)
@click.option("--version", default=None, type=int, help="Version to export (default: latest)")
@click.option("--output", "-o", default=None, help="Output file path")
@click.pass_context
def export_cb(ctx, fmt, version, output):
    """Export the codebook to YAML, JSON, or CSV."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    out = Path(output) if output else Path(f"codebook.{fmt}")
    export_codebook(conn, project["id"], out, format=fmt, version=version)
    conn.close()


@export.command("assignments")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]),
              default="csv", show_default=True)
@click.option("--agent", type=click.Choice(["a", "b", "supervisor"]), default=None,
              help="Filter to one agent (default: all)")
@click.option("--output", "-o", default=None)
@click.pass_context
def export_asgn(ctx, fmt, agent, output):
    """Export all code assignments."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    out = Path(output) if output else Path(f"assignments.{fmt}")
    export_assignments(conn, project["id"], out, format=fmt, agent_filter=agent)
    conn.close()


@export.command("memos")
@click.option("--format", "fmt", type=click.Choice(["md", "json"]),
              default="md", show_default=True)
@click.option("--output", "-o", default=None, help="Output directory")
@click.pass_context
def export_memo(ctx, fmt, output):
    """Export memos as Markdown files or JSON."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    out = Path(output) if output else Path("memos")
    export_memos(conn, project["id"], out, format=fmt)
    conn.close()


@export.command("llm-log")
@click.option("--call-type", default=None,
              help="Filter by call type: coding, calibration, induction, discussion, analysis")
@click.option("--agent", "agent_role", default=None,
              help="Filter by agent role: coder_a, coder_b, supervisor")
@click.option("--output", "-o", default="llm_calls.jsonl")
@click.pass_context
def export_log(ctx, call_type, agent_role, output):
    """Export the full LLM call audit log as JSONL."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    export_llm_log(conn, project["id"], Path(output),
                   call_type=call_type, agent_role=agent_role)
    conn.close()


@export.command("replication")
@click.option("--output", "-o", default=None,
              help="Output directory (default: <slug>_replication_<date>)")
@click.option("--skip-memo-gate", is_flag=True, default=False,
              help="Skip the reflexivity memo check before exporting")
@click.pass_context
def replication(ctx, output, skip_memo_gate):
    """
    Generate a complete replication package.

    Includes: codebook versions, all assignments, IRR results, memos,
    full LLM audit log (with prompts + responses), agent configs,
    prompt snapshots, and verification scripts.

    This package lets other researchers verify every coding decision
    and re-run any individual LLM call.

    Example:
        polyphony export replication --output ./my_study_replication/
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    # ── Reflexivity memo gate ────────────────────────────────────────────
    if not skip_memo_gate:
        from ..db import fetchall as _fetchall
        existing = fetchone(
            conn,
            "SELECT COUNT(*) AS n FROM memo WHERE project_id = ? AND memo_type = 'reflexivity'",
            (project["id"],),
        )
        if not existing or existing["n"] == 0:
            from rich.panel import Panel
            from rich.prompt import Confirm
            console.print(Panel(
                "[bold]No reflexivity memo found.[/]\n\n"
                "Before generating a replication package, we recommend writing\n"
                "a brief reflexivity memo documenting your positionality as the\n"
                "lead researcher — your disciplinary background, relationship\n"
                "to the data, and any relevant biases or perspectives.\n\n"
                "This memo will be included in the replication package and\n"
                "contributes to the Coder Card for the human supervisor.\n\n"
                "[dim]You can skip this with --skip-memo-gate[/]",
                title="[cyan]Reflexivity Memo Recommended[/]",
                border_style="cyan",
            ))
            if Confirm.ask("Write a reflexivity memo now?", default=True):
                import os, shlex, shutil, subprocess, tempfile
                with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
                    f.write(
                        "# Reflexivity Statement\n\n"
                        "<!-- Document your positionality as the lead researcher.\n"
                        "     Consider: disciplinary background, relationship to the data,\n"
                        "     relevant biases, expertise, insider/outsider status. -->\n\n"
                    )
                    tmp_path = f.name
                editor = os.environ.get("EDITOR", "nano")
                editor_parts = shlex.split(editor)
                _ALLOWED_EDITORS = {"nano", "vim", "vi", "nvim", "emacs", "code", "micro", "pico", "ee"}
                editor_basename = Path(editor_parts[0]).name
                if editor_basename not in _ALLOWED_EDITORS:
                    console.print(
                        f"[yellow]Editor '{editor_basename}' not in allow-list. Using nano.[/]"
                    )
                    editor_parts = ["nano"]
                editor_bin = shutil.which(editor_parts[0])
                if editor_bin:
                    subprocess.run([editor_bin, *editor_parts[1:], tmp_path], check=True)
                    content = Path(tmp_path).read_text().strip()
                    Path(tmp_path).unlink(missing_ok=True)
                    if content and not content.replace("# Reflexivity Statement", "").strip().startswith("<!--"):
                        from ..db import insert as _insert, json_col as _json_col
                        sup = fetchone(
                            conn,
                            "SELECT id FROM agent WHERE project_id = ? AND role = 'supervisor'",
                            (project["id"],),
                        )
                        _insert(conn, "memo", {
                            "project_id": project["id"],
                            "author_id": sup["id"] if sup else 1,
                            "memo_type": "reflexivity",
                            "title": "Reflexivity Statement",
                            "content": content,
                            "linked_codes": "[]",
                            "linked_segments": "[]",
                            "linked_flags": "[]",
                            "tags": _json_col(["reflexivity", "auto-gate"]),
                        })
                        conn.commit()
                        console.print("[green]✓ Reflexivity memo saved.[/]")
                    else:
                        console.print("[yellow]Empty memo — continuing without.[/]")
                else:
                    Path(tmp_path).unlink(missing_ok=True)
                    console.print("[yellow]No editor found — continuing without.[/]")

    if not output:
        date_str = datetime.now().strftime("%Y%m%d")
        output = f"{project['slug']}_replication_{date_str}"

    export_replication_package(conn, project["id"], Path(output))
    conn.close()
