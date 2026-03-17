"""polyphony IRR commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..db import connect, fetchall, fetchone

console = Console()


@click.group()
def irr():
    """Compute and inspect inter-rater reliability (IRR).

    IRR measures how consistently the two agents coded the same segments.
    Reports Krippendorff's alpha (primary), Cohen's kappa, and percent agreement.
    A Krippendorff's alpha of 0.80+ is conventionally considered acceptable.
    """


@irr.command("compute")
@click.option(
    "--scope",
    default="all",
    show_default=True,
    help="Scope: 'all', 'calibration', or 'code:<name>' (e.g. 'code:HOUSING_INSECURITY')",
)
@click.option("--run-a", default=None, type=int,
              help="Coding run ID for Agent A (default: latest independent run)")
@click.option("--run-b", default=None, type=int,
              help="Coding run ID for Agent B (default: latest independent run)")
@click.option("--notes", default="", help="Notes to attach to this IRR run")
@click.pass_context
def compute(ctx, scope, run_a, run_b, notes):
    """
    Compute inter-rater reliability between Coder A and Coder B.

    Reports Krippendorff's alpha, Cohen's kappa, and percent agreement.

    Example:
        polyphony irr compute
        polyphony irr compute --scope calibration
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    pid = project["id"]

    # Auto-detect latest runs if not specified
    def latest_run(role: str, run_type: str = "independent") -> int | None:
        row = fetchone(
            conn,
            """SELECT r.id FROM coding_run r JOIN agent a ON a.id = r.agent_id
               WHERE r.project_id = ? AND a.role = ? AND r.run_type = ?
                 AND r.status = 'complete'
               ORDER BY r.id DESC LIMIT 1""",
            (pid, role, run_type),
        )
        return row["id"] if row else None

    run_id_a = run_a or latest_run("coder_a")
    run_id_b = run_b or latest_run("coder_b")

    if not run_id_a or not run_id_b:
        console.print(
            "[red]Could not find completed coding runs for both agents.[/]\n"
            "Run [bold]polyphony code run[/] first."
        )
        conn.close()
        sys.exit(1)

    from ..pipeline.irr import compute_irr, print_irr_summary

    results = compute_irr(conn, pid, run_id_a, run_id_b, scope=scope, notes=notes or None)
    print_irr_summary(results)
    conn.close()


@irr.command("show")
@click.option("--run-id", default=None, type=int, help="Specific IRR run ID (default: latest)")
@click.pass_context
def show(ctx, run_id):
    """Show IRR results."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    if run_id:
        irr_run = fetchone(conn, "SELECT * FROM irr_run WHERE id = ?", (run_id,))
    else:
        irr_run = fetchone(
            conn,
            "SELECT * FROM irr_run WHERE project_id = ? ORDER BY computed_at DESC LIMIT 1",
            (project["id"],),
        )

    if not irr_run:
        console.print("[dim]No IRR runs found.[/]")
        conn.close()
        return

    from ..pipeline.irr import print_irr_summary
    print_irr_summary({
        "krippendorff_alpha": irr_run["krippendorff_alpha"],
        "cohen_kappa": irr_run["cohen_kappa"],
        "percent_agreement": irr_run["percent_agreement"],
        "segment_count": irr_run["segment_count"],
        "disagreement_count": irr_run["disagreement_count"],
        "scope": irr_run["scope"],
    })
    conn.close()


@irr.command("disagreements")
@click.option("--run-id", default=None, type=int)
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def disagreements(ctx, run_id, limit):
    """List segments where coders disagreed."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    if not run_id:
        latest = fetchone(
            conn,
            "SELECT id FROM irr_run WHERE project_id = ? ORDER BY computed_at DESC LIMIT 1",
            (project["id"],),
        )
        if not latest:
            console.print("[dim]No IRR runs.[/]")
            conn.close()
            return
        run_id = latest["id"]

    rows = fetchall(
        conn,
        """SELECT d.*, seg.text AS segment_text
           FROM irr_disagreement d
           JOIN segment seg ON seg.id = d.segment_id
           WHERE d.irr_run_id = ?
           LIMIT ?""",
        (run_id, limit),
    )
    conn.close()

    if not rows:
        console.print("[green]No disagreements found.[/]")
        return

    for d in rows:
        console.print(Panel(
            f"[dim]{(d['segment_text'] or '')[:200]}...[/]\n\n"
            f"  Coder A: [green]{d['code_a'] or '(nothing)'}[/]\n"
            f"  Coder B: [yellow]{d['code_b'] or '(nothing)'}[/]\n"
            f"  Resolution: {d.get('resolution') or '[dim]unresolved[/]'}",
            title=f"[cyan]Segment {d['segment_id']}[/]",
            border_style="dim",
        ))
