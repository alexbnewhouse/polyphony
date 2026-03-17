"""polyphony analysis commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console

from ..db import connect, fetchone
from ..pipeline.analysis import (
    check_saturation,
    code_frequency_table,
    co_occurrence_matrix,
    print_code_frequency,
    synthesize_themes,
)
from ..utils import build_agent_objects, get_active_codebook

console = Console()


@click.group()
def analyze():
    """Analyze coded data for patterns and themes."""


@analyze.command("frequencies")
@click.pass_context
def frequencies(ctx):
    """Show code frequency across the corpus."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    rows = code_frequency_table(conn, project["id"])
    conn.close()
    print_code_frequency(rows)


@analyze.command("saturation")
@click.option("--window", default=20, show_default=True,
              help="Window size for saturation check")
@click.pass_context
def saturation(ctx, window):
    """
    Check for theoretical saturation.

    Compares the rate of new codes in the first vs. later portions of
    the corpus. A declining rate suggests saturation.
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    result = check_saturation(conn, project["id"], window_size=window)
    conn.close()

    console.print(f"\n[bold]Saturation Analysis[/]")
    console.print(f"  Total unique codes: {result['total_unique_codes']}")
    console.print(f"  Window size: {result['window_size']} segments")
    console.print(f"\n  New codes per window:")
    for i, n in enumerate(result["new_codes_per_window"]):
        bar = "█" * n
        console.print(f"    Window {i + 1:2d}: {bar} ({n})")

    if result["likely_saturated"]:
        console.print(
            "\n[green]✓ Likely saturated: no new codes in the final window.[/]"
        )
    else:
        console.print(
            "\n[yellow]New codes still emerging — consider extending the corpus.[/]"
        )


@analyze.command("themes")
@click.option("--agent", type=click.Choice(["a", "b"]), default="a",
              help="Which agent synthesizes themes")
@click.pass_context
def themes(ctx, agent):
    """
    Use an LLM agent to synthesize analytical themes from the coded corpus.

    The agent reads the full codebook + frequency data and produces a
    narrative synthesis you can use as a starting point for writing.

    Example:
        polyphony analyze themes --agent a
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    agent_a, agent_b, _ = build_agent_objects(conn, project["id"])
    cb = get_active_codebook(conn, project["id"])

    if not cb:
        console.print("[red]No codebook.[/]")
        conn.close()
        return

    chosen_agent = agent_a if agent == "a" else agent_b
    synthesis = synthesize_themes(chosen_agent, conn, project, cb["id"])
    conn.close()

    from rich.panel import Panel
    console.print(Panel(synthesis, title="[bold cyan]Theme Synthesis[/]"))
    console.print(
        "\n[dim]Tip: Save this as a memo with "
        "`polyphony memo new --type synthesis --title 'Theme synthesis'`[/]"
    )


@analyze.command("co-occurrence")
@click.option("--top", default=10, show_default=True, help="Show top N co-occurring pairs")
@click.pass_context
def co_occurrence(ctx, top):
    """Show most frequent code co-occurrences (codes appearing in same segment)."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    matrix = co_occurrence_matrix(conn, project["id"])
    conn.close()

    # Flatten and sort
    pairs = []
    seen = set()
    for ca, targets in matrix.items():
        for cb, n in targets.items():
            key = tuple(sorted([ca, cb]))
            if key not in seen:
                pairs.append((ca, cb, n))
                seen.add(key)
    pairs.sort(key=lambda x: -x[2])

    from rich.table import Table
    table = Table(title=f"Top {top} Code Co-occurrences")
    table.add_column("Code A")
    table.add_column("Code B")
    table.add_column("Segments", justify="right")
    for ca, cb, n in pairs[:top]:
        table.add_row(ca, cb, str(n))
    console.print(table)
