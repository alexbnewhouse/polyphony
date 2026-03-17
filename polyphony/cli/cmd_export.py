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
@click.option("--agent", type=click.Choice(["a", "b"]), default=None,
              help="Filter to one agent (default: both)")
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
@click.pass_context
def replication(ctx, output):
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

    if not output:
        date_str = datetime.now().strftime("%Y%m%d")
        output = f"{project['slug']}_replication_{date_str}"

    export_replication_package(conn, project["id"], Path(output))
    conn.close()
