"""polyphony calibration commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from ..db import connect, fetchone
from ..utils import build_agent_objects, get_active_codebook

console = Console()


@click.group()
def calibrate():
    """Run calibration rounds to align coders before independent coding.

    \b
    WORKFLOW:
      Both agents code the same small sample ("calibration set"), then
      Krippendorff's alpha is computed. If alpha < threshold, you review
      disagreements and can refine the codebook before trying again.
      Repeat until alpha meets the threshold (default: 0.80).
    """


@calibrate.command("run")
@click.option("--sample-size", "-n", default=10, show_default=True,
              type=click.IntRange(2, 500), help="Number of calibration segments (2–500)")
@click.option("--threshold", default=0.80, show_default=True,
              type=click.FloatRange(0.0, 1.0), help="Minimum Krippendorff alpha to proceed")
@click.option("--max-rounds", default=5, show_default=True,
              help="Maximum calibration rounds before proceeding regardless")
@click.option("--reset", is_flag=True,
              help="Re-select a new calibration set (clears the existing one)")
@click.option("--include-supervisor", is_flag=True, default=False,
              help="Include the supervisor as a third coder for 3-way calibration")
@click.option("--batch", is_flag=True, default=False,
              help="Batch multiple segments per LLM call (faster, uses context window)")
@click.pass_context
def run(ctx, sample_size, threshold, max_rounds, reset, include_supervisor, batch):
    """
    Run calibration: both agents code the same segment sample, then you
    review disagreements and optionally refine the codebook.

    With --include-supervisor, the human codes the calibration set as a
    third coder and 3-way Krippendorff's alpha is computed.

    Repeats until Krippendorff's alpha meets --threshold or --max-rounds is reached.

    \b
    Examples:
        polyphony calibrate run
        polyphony calibrate run --sample-size 15 --threshold 0.80
        polyphony calibrate run --include-supervisor  # 3-way calibration
        polyphony calibrate run --reset   # pick a fresh calibration sample
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    cb = get_active_codebook(conn, project["id"])

    if not cb:
        console.print(
            "[red]No codebook found.[/]\n"
            "Run [bold]polyphony codebook induce[/] first."
        )
        conn.close()
        sys.exit(1)

    if reset:
        if not Confirm.ask(
            "[yellow]--reset will clear the existing calibration set and pick a new sample. "
            "Previous calibration coding will no longer be linked. Continue?[/]",
            default=False,
        ):
            console.print("[dim]Cancelled.[/]")
            conn.close()
            return

    agent_a, agent_b, supervisor = build_agent_objects(conn, project["id"])

    from ..pipeline.calibration import run_calibration

    run_calibration(
        conn=conn,
        project=project,
        agent_a=agent_a,
        agent_b=agent_b,
        codebook_version_id=cb["id"],
        irr_threshold=threshold,
        calibration_sample_size=sample_size,
        max_rounds=max_rounds,
        include_supervisor=include_supervisor,
        supervisor_agent=supervisor,
        batch=batch,
    )
    conn.close()

    console.print(Panel(
        "Calibration complete.\n\n"
        "Next steps:\n"
        "  [bold]polyphony code run[/]          ← independent coding by both agents\n"
        "  [bold]polyphony codebook show[/]      ← review the refined codebook\n"
        "  [bold]polyphony codebook edit <name>[/] ← make further adjustments",
        title="[bold green]What's Next[/]",
        border_style="green",
    ))
