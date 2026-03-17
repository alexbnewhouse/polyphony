"""polyphony coding session commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..db import connect, fetchall, fetchone
from ..utils import build_agent_objects, get_active_codebook

console = Console()


@click.group()
def code():
    """Run independent coding sessions.

    Each agent codes every segment in isolation — neither agent sees the
    other's assignments during their run. This enforces independence for
    valid inter-rater reliability (IRR) measurement.
    """


@code.command("run")
@click.option(
    "--agent",
    type=click.Choice(["a", "b", "both"]),
    default="both",
    show_default=True,
    help="Which agent(s) to run",
)
@click.option("--resume", is_flag=True, help="Resume an interrupted coding run")
@click.option("--calibration-only", is_flag=True,
              help="Code only the calibration set (useful for testing)")
@click.pass_context
def run(ctx, agent, resume, calibration_only):
    """
    Run independent coding: each agent codes every segment using the codebook.

    Agents are strictly isolated — neither sees the other's assignments
    during their coding run.

    Examples:
        polyphony code run              # Both agents code everything
        polyphony code run --agent a    # Only Coder A
        polyphony code run --resume     # Continue an interrupted run
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    cb = get_active_codebook(conn, project["id"])

    if not cb:
        console.print("[red]No codebook. Run `polyphony codebook induce` first.[/]")
        conn.close()
        sys.exit(1)

    agent_a, agent_b, _ = build_agent_objects(conn, project["id"])

    # Verify Ollama models are available before starting
    for label, ag in [("A", agent_a), ("B", agent_b)]:
        if agent in ("both",) or (agent == "a" and label == "A") or (agent == "b" and label == "B"):
            if hasattr(ag, "is_available") and not ag.is_available():
                console.print(
                    f"[red]Model '{ag.model_name}' (Coder {label}) not found in Ollama.[/]\n"
                    f"Run: [bold]ollama pull {ag.model_name}[/bold]"
                )
                conn.close()
                sys.exit(1)

    from ..pipeline.coding import run_coding_session

    run_type = "calibration" if calibration_only else "independent"

    if agent in ("a", "both"):
        run_coding_session(
            conn=conn, project=project, agent=agent_a,
            codebook_version_id=cb["id"],
            run_type=run_type, resume=resume,
        )

    if agent in ("b", "both"):
        run_coding_session(
            conn=conn, project=project, agent=agent_b,
            codebook_version_id=cb["id"],
            run_type=run_type, resume=resume,
        )

    if not calibration_only:
        conn.execute(
            "UPDATE project SET status='irr', updated_at=datetime('now') WHERE id=?",
            (project["id"],),
        )
        conn.commit()

    conn.close()

    if not calibration_only:
        console.print(Panel(
            "Independent coding complete.\n\n"
            "Next steps:\n"
            "  [bold]polyphony irr compute[/]         ← compute inter-rater reliability\n"
            "  [bold]polyphony irr disagreements[/]    ← review where agents differed\n"
            "  [bold]polyphony discuss flags[/]        ← resolve flagged segments",
            title="[bold green]What's Next[/]",
            border_style="green",
        ))


@code.command("status")
@click.pass_context
def status(ctx):
    """Show coding progress across all segments."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    n_total = fetchone(conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?",
                       (project["id"],))["n"]

    runs = fetchall(
        conn,
        """SELECT r.id, r.run_type, r.status, ag.role, ag.model_name,
                  r.started_at, r.completed_at,
                  COUNT(DISTINCT a.segment_id) AS coded_segments
           FROM coding_run r
           JOIN agent ag ON ag.id = r.agent_id
           LEFT JOIN assignment a ON a.coding_run_id = r.id
           WHERE r.project_id = ?
           GROUP BY r.id
           ORDER BY r.id""",
        (project["id"],),
    )
    conn.close()

    if not runs:
        console.print("[dim]No coding runs yet.[/]")
        return

    table = Table(title=f"Coding Progress (total segments: {n_total})")
    table.add_column("Run ID")
    table.add_column("Type")
    table.add_column("Agent")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Coded", justify="right")
    table.add_column("% Done", justify="right")

    for r in runs:
        pct = f"{r['coded_segments'] / n_total * 100:.0f}%" if n_total else "n/a"
        table.add_row(
            str(r["id"]), r["run_type"], r["role"], r["model_name"] or "",
            r["status"], str(r["coded_segments"]), pct,
        )
    console.print(table)


@code.command("show")
@click.argument("segment_id", type=int)
@click.pass_context
def show_segment(ctx, segment_id):
    """Show all codes assigned to a segment across all coders."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    seg = fetchone(conn, "SELECT * FROM segment WHERE id = ?", (segment_id,))
    if not seg:
        console.print(f"[red]Segment {segment_id} not found.[/]")
        conn.close()
        return

    assignments = fetchall(
        conn,
        """SELECT a.*, c.name AS code_name, c.level,
                  ag.role, r.run_type
           FROM assignment a
           JOIN code c ON c.id = a.code_id
           JOIN agent ag ON ag.id = a.agent_id
           JOIN coding_run r ON r.id = a.coding_run_id
           WHERE a.segment_id = ?
           ORDER BY ag.role, c.name""",
        (segment_id,),
    )
    conn.close()

    from rich.panel import Panel
    console.print(Panel(seg["text"], title=f"[cyan]Segment {segment_id}[/]"))

    if not assignments:
        console.print("[dim]No codes assigned yet.[/]")
        return

    table = Table(show_header=True)
    table.add_column("Agent")
    table.add_column("Code")
    table.add_column("Level")
    table.add_column("Confidence")
    table.add_column("Rationale")
    for a in assignments:
        table.add_row(
            a["role"], a["code_name"], a["level"],
            f"{a['confidence']:.2f}" if a["confidence"] is not None else "",
            (a["rationale"] or "")[:60],
        )
    console.print(table)
