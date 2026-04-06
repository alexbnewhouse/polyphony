"""
polyphony CLI — main entry point.

All sub-commands are registered here. The CLI resolves the active project
from either:
  1. The --project option (a slug)
  2. A .polyphony_project marker file in the current directory (written by `polyphony project open`)

Usage:
  polyphony project new --name "Housing Study"
  polyphony data import interview_01.txt interview_02.txt
  polyphony codebook induce
  polyphony calibrate run
  polyphony code run
  polyphony irr compute
  polyphony discuss flags
  polyphony export replication
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from ..db import find_project_db, get_projects_root, project_db_path
from .cmd_project import project
from .cmd_data import data
from .cmd_codebook import codebook
from .cmd_calibrate import calibrate
from .cmd_code import code
from .cmd_irr import irr
from .cmd_discuss import discuss
from .cmd_memo import memo
from .cmd_analyze import analyze
from .cmd_export import export
from .cmd_practice import practice
from .cmd_setup import setup

console = Console()


@click.group()
@click.version_option(package_name="polyphony")
@click.option(
    "--project",
    "project_slug",
    default=None,
    help="Project slug — short identifier for the project (overrides .polyphony_project marker).",
)
@click.pass_context
def cli(ctx: click.Context, project_slug: str | None) -> None:
    """
    polyphony — Collaborative qualitative data analysis.

    Human + two local LLM coders working together on your research project.

    \b
    TYPICAL WORKFLOW:
      1. polyphony project new --name "My Study"   # Create project
      2. polyphony data import files/*.txt          # Import documents
      3. polyphony codebook induce                  # Generate initial codebook
      4. polyphony calibrate run                    # Align coders (iterative)
      5. polyphony code run                         # Independent coding
      6. polyphony irr compute                      # Inter-rater reliability
      7. polyphony discuss flags                    # Resolve disagreements
      8. polyphony analyze themes                   # Synthesize findings
      9. polyphony export replication               # Archive for publication
    
        NOTE: To choose the LLMs used for the two coders when creating a project,
        pass the `--model-a` and `--model-b` flags to `polyphony project new`.
        Example: `polyphony project new --name "My Study" --model-a llama3.1:8b --model-b llama3.2:3b`.
        """
    ctx.ensure_object(dict)
    projects_root = get_projects_root()
    ctx.obj["projects_root"] = projects_root

    # Resolve active project DB
    if project_slug:
        db_path = project_db_path(projects_root, project_slug)
        if not db_path.exists():
            # Don't abort for project/new or project/list commands
            ctx.obj["db_path"] = db_path
            ctx.obj["project_slug"] = project_slug
            return
    else:
        try:
            db_path = find_project_db()
        except FileNotFoundError:
            db_path = None

    ctx.obj["db_path"] = db_path
    ctx.obj["project_slug"] = project_slug


# Register sub-command groups
cli.add_command(project)
cli.add_command(data)
cli.add_command(codebook)
cli.add_command(calibrate)
cli.add_command(code)
cli.add_command(irr)
cli.add_command(discuss)
cli.add_command(memo)
cli.add_command(analyze)
cli.add_command(export)
cli.add_command(practice)
cli.add_command(setup)


def require_db(ctx: click.Context) -> Path:
    """Get DB path from context, or abort with a helpful message."""
    from rich.panel import Panel
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print(Panel(
            "[red]No active project found.[/]\n\n"
            "To start a new project:\n"
            "  [bold]polyphony project new --name \"My Study\"[/]\n\n"
            "To reopen an existing project:\n"
            "  [bold]polyphony project list[/]          ← see available projects\n"
            "  [bold]polyphony project open <slug>[/]    ← set as active",
            title="[bold red]No Active Project[/]",
            border_style="red",
        ))
        sys.exit(1)
    return db_path


if __name__ == "__main__":
    cli()
