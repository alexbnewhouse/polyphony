"""polyphony analysis commands."""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console

from ..db import connect, fetchall, fetchone, from_json
from ..pipeline.analysis import (
    check_saturation,
    code_frequency_by_document,
    code_frequency_table,
    co_occurrence_matrix,
    print_code_frequency,
    speaker_frequency_table,
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


@analyze.command("frequencies-by-doc")
@click.pass_context
def frequencies_by_doc(ctx):
    """Show code frequency broken down by document (e.g. per episode).

    Useful for comparing how codes are distributed across different
    podcast episodes or interview transcripts.
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    rows = code_frequency_by_document(conn, project["id"])
    conn.close()

    if not rows:
        console.print("[dim]No coding data found.[/]")
        return

    from rich.table import Table
    table = Table(title="Code Frequency by Document", show_header=True)
    table.add_column("Document", style="bold")
    table.add_column("Code", style="cyan")
    table.add_column("Segments", justify="right")

    current_doc = None
    for r in rows:
        doc_label = r["filename"] if r["filename"] != current_doc else ""
        current_doc = r["filename"]
        table.add_row(doc_label, r["code_name"], str(r["segment_count"]))
    console.print(table)


@analyze.command("speaker-codes")
@click.pass_context
def speaker_codes(ctx):
    """Show code frequency broken down by speaker label.

    Only available for transcripts that were imported with speaker
    diarization. Shows how codes are distributed across speakers.
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    rows = speaker_frequency_table(conn, project["id"])
    conn.close()

    if not rows:
        console.print("[dim]No speaker-coded data found. Did you import with --diarize?[/]")
        return

    from rich.table import Table
    table = Table(title="Code Frequency by Speaker", show_header=True)
    table.add_column("Speaker", style="bold")
    table.add_column("Code", style="cyan")
    table.add_column("Segments", justify="right")

    current_speaker = None
    for r in rows:
        speaker_label = r["speaker"] if r["speaker"] != current_speaker else ""
        current_speaker = r["speaker"]
        table.add_row(speaker_label, r["code_name"], str(r["segment_count"]))
    console.print(table)


@analyze.command("engagement")
@click.pass_context
def engagement(ctx):
    """Show a dashboard of human engagement with the analytical process.

    Displays memo counts, flag resolution stats, blind assessments,
    codebook review stats, and calibration history — a quick self-audit
    of how actively the human researcher has engaged.
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    pid = project["id"]

    from rich.panel import Panel
    from rich.table import Table

    # ── Memo counts ──────────────────────────────────────────────────────
    memo_rows = fetchall(
        conn,
        "SELECT memo_type, COUNT(*) AS n FROM memo WHERE project_id = ? GROUP BY memo_type",
        (pid,),
    )
    memo_counts = {r["memo_type"]: r["n"] for r in memo_rows}
    total_memos = sum(memo_counts.values())

    # ── Flag stats ───────────────────────────────────────────────────────
    total_flags = fetchone(
        conn, "SELECT COUNT(*) AS n FROM flag WHERE project_id = ?", (pid,),
    )["n"]
    resolved_flags = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM flag WHERE project_id = ? AND status = 'resolved'",
        (pid,),
    )["n"]
    deferred_flags = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM flag WHERE project_id = ? AND status = 'deferred'",
        (pid,),
    )["n"]
    blind_coded = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM flag WHERE project_id = ? AND supervisor_blind_code IS NOT NULL",
        (pid,),
    )["n"]

    # ── Human coding stats ───────────────────────────────────────────────
    sup_assignments = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM assignment a "
        "JOIN agent ag ON ag.id = a.agent_id "
        "JOIN coding_run r ON r.id = a.coding_run_id "
        "WHERE r.project_id = ? AND ag.role = 'supervisor'",
        (pid,),
    )["n"]
    total_segments = fetchone(
        conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?", (pid,),
    )["n"]

    # ── Codebook review stats ────────────────────────────────────────────
    latest_cb = fetchone(
        conn,
        "SELECT * FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (pid,),
    )
    review_stats = from_json(latest_cb.get("review_stats"), {}) if latest_cb else {}

    # ── Calibration history ──────────────────────────────────────────────
    cal_runs = fetchall(
        conn,
        "SELECT * FROM irr_run WHERE project_id = ? AND scope = 'calibration' ORDER BY computed_at",
        (pid,),
    )

    conn.close()

    # ── Render ───────────────────────────────────────────────────────────
    table = Table(title="Human Engagement Dashboard", show_header=True, title_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Note", style="dim")

    # Memos
    table.add_row("Total memos", str(total_memos), "")
    for mtype, n in sorted(memo_counts.items()):
        table.add_row(f"  └ {mtype}", str(n), "")

    # Flags
    table.add_row("Flags raised", str(total_flags), "")
    table.add_row("  └ resolved", str(resolved_flags), "")
    table.add_row("  └ deferred", str(deferred_flags), "genuinely ambiguous")
    pct_blind = f"{blind_coded / total_flags:.0%}" if total_flags else "N/A"
    table.add_row("  └ blind-assessed", str(blind_coded), pct_blind)

    # Human coding
    pct_coded = f"{sup_assignments / total_segments:.0%}" if total_segments else "N/A"
    table.add_row("Segments human-coded", str(sup_assignments), f"of {total_segments} ({pct_coded})")

    # Codebook review
    if review_stats:
        table.add_row("Codebook review", "", "")
        table.add_row("  └ accepted verbatim", str(review_stats.get("accepted_verbatim", 0)), "")
        table.add_row("  └ edited", str(review_stats.get("edited", 0)), "")
        table.add_row("  └ rejected", str(review_stats.get("rejected", 0)), "")
        table.add_row("  └ added manually", str(review_stats.get("added_manually", 0)), "")
    else:
        table.add_row("Codebook review", "—", "no induction review recorded")

    # Calibration
    if cal_runs:
        alphas = [r.get("krippendorff_alpha") for r in cal_runs if r.get("krippendorff_alpha") is not None]
        alpha_str = " → ".join(f"{a:.3f}" for a in alphas) if alphas else "—"
        table.add_row("Calibration rounds", str(len(cal_runs)), alpha_str)
    else:
        table.add_row("Calibration rounds", "0", "")

    console.print()
    console.print(table)

    # Quick assessment
    has_reflexivity = memo_counts.get("reflexivity", 0) > 0
    has_method_memos = memo_counts.get("methodological", 0) > 0

    warnings = []
    if not has_reflexivity:
        warnings.append("No reflexivity memo found — consider writing one before export.")
    if not has_method_memos:
        warnings.append("No methodological memos — document your analytical decisions.")
    if total_flags > 0 and blind_coded == 0:
        warnings.append("No blind assessments recorded — use agent_facilitated mode for commit-then-reveal.")
    if sup_assignments == 0:
        warnings.append("No human-coded segments — consider coding a sample for 3-way IRR.")

    if warnings:
        console.print()
        for w in warnings:
            console.print(f"  [yellow]⚠ {w}[/]")
    else:
        console.print("\n  [green]✓ Strong human engagement across the board.[/]")
    console.print()
