"""polyphony project management commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from ..db import connect, fetchall, fetchone, insert, json_col, project_db_path, write_project_marker
from ..utils import slugify

console = Console()


@click.group()
def project():
    """Create, open, and inspect projects."""


@project.command("new")
@click.option("--name", "-n", required=True, help="Project name (e.g. 'Housing Precarity Study')")
@click.option("--description", "-d", default="", help="Short project description")
@click.option(
    "--methodology",
    "-m",
    type=click.Choice(["grounded_theory", "thematic_analysis", "content_analysis"]),
    default="grounded_theory",
    show_default=True,
    help="QDA methodology",
)
@click.option(
    "--model-a",
    default="llama3.1:8b",
    show_default=True,
    help="Model for Coder A (e.g. 'llama3.1:8b', 'gpt-4o', 'claude-sonnet-4-5-20250514')",
)
@click.option(
    "--model-b",
    default="llama3.1:8b",
    show_default=True,
    help="Model for Coder B (can be same model, different seed)",
)
@click.option(
    "--provider-a",
    type=click.Choice(["ollama", "openai", "anthropic"]),
    default="ollama",
    show_default=True,
    help="LLM provider for Coder A",
)
@click.option(
    "--provider-b",
    type=click.Choice(["ollama", "openai", "anthropic"]),
    default="ollama",
    show_default=True,
    help="LLM provider for Coder B",
)
@click.option("--seed-a", default=42, show_default=True, help="Random seed for Coder A")
@click.option("--seed-b", default=137, show_default=True, help="Random seed for Coder B")
@click.option("--temperature", "-t", default=0.1, show_default=True,
              type=click.FloatRange(0.0, 1.0), help="LLM temperature (0=deterministic, 1=creative)")
@click.pass_context
def new(ctx, name, description, methodology, model_a, model_b, provider_a, provider_b, seed_a, seed_b, temperature):
    """
    Create a new QDA project.

    Example:
        polyphony project new --name "Housing Precarity 2026" --methodology grounded_theory
    """
    projects_root: Path = ctx.obj["projects_root"]
    slug = slugify(name)

    db_path = project_db_path(projects_root, slug)
    if db_path.exists():
        console.print(f"[red]Project '{slug}' already exists at {db_path}[/]")
        sys.exit(1)

    # Collect research questions interactively
    console.print(Panel(
        "Enter your research questions (one per line).\n"
        "Press Enter on an empty line when done.\n\n"
        "[dim]Examples:\n"
        "  How do residents describe housing insecurity?\n"
        "  What coping strategies emerge in interview data?\n"
        "  How does precarity discourse shift across time periods?[/]",
        title="[cyan]Research Questions[/]",
    ))
    rqs = []
    while True:
        q = Prompt.ask(f"  RQ {len(rqs) + 1}", default="")
        if not q:
            break
        rqs.append(q)

    conn = connect(db_path)

    # Create project
    project_id = insert(conn, "project", {
        "name": name,
        "slug": slug,
        "description": description or None,
        "methodology": methodology,
        "research_questions": json_col(rqs),
        "status": "setup",
        "config": "{}",
    })

    # Create agents
    insert(conn, "agent", {
        "project_id": project_id,
        "role": "supervisor",
        "agent_type": "human",
        "model_name": "human",
        "model_version": "human",
        "temperature": 0.0,
        "seed": 0,
        "system_prompt": "Human supervisor",
    })

    # Map CLI provider names to agent_type values stored in DB
    _PROVIDER_TO_AGENT_TYPE = {"ollama": "llm", "openai": "openai", "anthropic": "anthropic"}

    # Coder A
    insert(conn, "agent", {
        "project_id": project_id,
        "role": "coder_a",
        "agent_type": _PROVIDER_TO_AGENT_TYPE[provider_a],
        "model_name": model_a,
        "model_version": "unknown",
        "temperature": temperature,
        "seed": seed_a,
        "system_prompt": None,
    })

    # Coder B
    insert(conn, "agent", {
        "project_id": project_id,
        "role": "coder_b",
        "agent_type": _PROVIDER_TO_AGENT_TYPE[provider_b],
        "model_name": model_b,
        "model_version": "unknown",
        "temperature": temperature,
        "seed": seed_b,
        "system_prompt": None,
    })

    conn.commit()
    conn.close()

    # Write marker in current directory
    write_project_marker(Path.cwd(), db_path.parent)

    console.print(
        Panel(
            f"[bold green]Project created![/]\n\n"
            f"  Name:        {name}\n"
            f"  Slug:        {slug}  [dim](used to reopen: polyphony project open {slug})[/]\n"
            f"  Methodology: {methodology}\n"
            f"  Coder A:     {model_a} via {provider_a} (seed={seed_a})\n"
            f"  Coder B:     {model_b} via {provider_b} (seed={seed_b})\n"
            f"  DB:          {db_path}\n\n"
            f"Next step: [bold]polyphony data import <files...>[/]",
            title="[bold cyan]polyphony[/]",
        )
    )


@project.command("open")
@click.argument("slug")
@click.pass_context
def open_project(ctx, slug):
    """Set a project as active in the current directory.

    SLUG is the short identifier for the project (e.g. 'housing-precarity-2026').
    Run 'polyphony project list' to see all slugs.
    """
    projects_root: Path = ctx.obj["projects_root"]
    db_path = project_db_path(projects_root, slug)
    if not db_path.exists():
        console.print(f"[red]Project '{slug}' not found at {db_path}[/]")
        sys.exit(1)
    write_project_marker(Path.cwd(), db_path.parent)
    console.print(f"[green]Active project: {slug}[/]")


@project.command("list")
@click.pass_context
def list_projects(ctx):
    """List all projects."""
    projects_root: Path = ctx.obj["projects_root"]
    if not projects_root.exists():
        console.print("[dim]No projects found.[/]")
        return

    table = Table(title="Projects", show_header=True)
    table.add_column("Slug", style="bold")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Methodology")
    table.add_column("Created")

    for project_dir in sorted(projects_root.iterdir()):
        db = project_dir / "project.db"
        if not db.exists():
            continue
        conn = connect(db)
        p = fetchone(conn, "SELECT * FROM project WHERE id = 1")
        conn.close()
        if p:
            table.add_row(
                p["slug"],
                p["name"],
                p["status"],
                p["methodology"],
                (p.get("created_at") or "")[:10],
            )

    console.print(table)


@project.command("status")
@click.pass_context
def status(ctx):
    """Show detailed status of the active project."""
    db_path = ctx.obj.get("db_path")
    if not db_path or not Path(db_path).exists():
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    p = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    if not p:
        console.print("[red]Project database is empty.[/]")
        conn.close()
        return

    rqs = json.loads(p.get("research_questions") or "[]")

    n_docs = fetchone(conn, "SELECT COUNT(*) AS n FROM document WHERE project_id = ?", (p["id"],))["n"]
    n_segs = fetchone(conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?", (p["id"],))["n"]
    n_codes = fetchone(conn, "SELECT COUNT(*) AS n FROM code WHERE project_id = ? AND is_active = 1", (p["id"],))["n"]
    n_asgn = fetchone(conn, "SELECT COUNT(*) AS n FROM assignment WHERE coding_run_id IN (SELECT id FROM coding_run WHERE project_id = ?)", (p["id"],))["n"]
    n_flags = fetchone(conn, "SELECT COUNT(*) AS n FROM flag WHERE project_id = ? AND status = 'open'", (p["id"],))["n"]
    n_memos = fetchone(conn, "SELECT COUNT(*) AS n FROM memo WHERE project_id = ?", (p["id"],))["n"]
    conn.close()

    rq_text = "\n".join(f"  • {q}" for q in rqs) or "  (not specified)"

    STATUS_PIPELINE = [
        "setup", "importing", "inducing", "calibrating",
        "coding", "irr", "discussing", "analyzing", "done"
    ]
    current_idx = STATUS_PIPELINE.index(p["status"]) if p["status"] in STATUS_PIPELINE else 0
    pipeline_display = " → ".join(
        f"[bold green]{s}[/]" if s == p["status"] else
        f"[dim]{s}[/]" if STATUS_PIPELINE.index(s) < current_idx else s
        for s in STATUS_PIPELINE
    )

    console.print(Panel(
        f"[bold]{p['name']}[/] (slug: {p['slug']})\n"
        f"Methodology: {p['methodology']}\n\n"
        f"Research questions:\n{rq_text}\n\n"
        f"Pipeline: {pipeline_display}\n\n"
        f"  Documents:   {n_docs}\n"
        f"  Segments:    {n_segs}\n"
        f"  Codes:       {n_codes}\n"
        f"  Assignments: {n_asgn}\n"
        f"  Open flags:  {n_flags}\n"
        f"  Memos:       {n_memos}",
        title="[bold cyan]Project Status[/]",
    ))
