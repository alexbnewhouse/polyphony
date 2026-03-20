"""
polyphony.pipeline.calibration
=========================
Calibration pipeline.

Calibration is a structured exercise where both agents code the same
small set of segments ("calibration set"), then disagreements are reviewed
and resolved to align coders before full independent coding begins.

Workflow:
  1. Mark a sample of segments as is_calibration = 1.
  2. Run both agents on the calibration set.
  3. Compute IRR.
  4. If alpha >= threshold, proceed.
  5. Otherwise, present each disagreement to supervisor for resolution,
     optionally having agents explain their reasoning first.
  6. Supervisor refines codebook; a new version can be saved.
  7. Repeat until threshold is met or supervisor proceeds manually.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..db import fetchall, fetchone, insert, json_col
from ..pipeline.coding import run_coding_session
from ..pipeline.irr import (
    compute_irr, compute_irr_multiway, print_irr_summary,
    find_disagreements, find_disagreements_multiway,
    get_coding_matrix, get_coding_matrices,
)
from ..prompts import library as prompt_lib

console = Console()

DEFAULT_IRR_THRESHOLD = 0.80


# ─────────────────────────────────────────────────────────────────────────────
# Mark calibration set
# ─────────────────────────────────────────────────────────────────────────────


def mark_calibration_set(
    conn: sqlite3.Connection,
    project_id: int,
    n: int = 10,
    seed: int = 99,
    clear_existing: bool = False,
) -> int:
    """
    Mark `n` segments as the calibration set (is_calibration = 1).
    Stratified by document. Returns the number of segments marked.
    """
    if clear_existing:
        conn.execute(
            "UPDATE segment SET is_calibration = 0 WHERE project_id = ?",
            (project_id,),
        )

    # Don't re-mark if already done
    existing = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ? AND is_calibration = 1",
        (project_id,),
    )
    if existing["n"] > 0 and not clear_existing:
        console.print(f"[dim]Calibration set already marked ({existing['n']} segments).[/]")
        return existing["n"]

    docs = fetchall(
        conn,
        "SELECT DISTINCT document_id FROM segment WHERE project_id = ?",
        (project_id,),
    )
    per_doc = max(1, n // len(docs))
    rng = random.Random(seed)
    marked_ids = []

    for doc_row in docs:
        segs = fetchall(
            conn,
            "SELECT id FROM segment WHERE project_id = ? AND document_id = ?",
            (project_id, doc_row["document_id"]),
        )
        chosen = rng.sample(segs, min(per_doc, len(segs)))
        marked_ids.extend(s["id"] for s in chosen)

    marked_ids = marked_ids[:n]
    for seg_id in marked_ids:
        conn.execute("UPDATE segment SET is_calibration = 1 WHERE id = ?", (seg_id,))
    conn.commit()

    console.print(f"Marked {len(marked_ids)} segments as calibration set.")
    return len(marked_ids)


# ─────────────────────────────────────────────────────────────────────────────
# Discussion of a single disagreement
# ─────────────────────────────────────────────────────────────────────────────


def discuss_disagreement(
    conn: sqlite3.Connection,
    project: dict,
    segment: dict,
    codes_a: List[str],
    codes_b: List[str],
    agent_a,
    agent_b,
    flag_id: Optional[int] = None,
    codes_c: Optional[List[str]] = None,
) -> str:
    """
    Facilitate a structured discussion between agents A and B about a
    disagreement. Optionally shows supervisor's codes (codes_c) for 3-way view.
    Returns the supervisor's resolution choice.
    """
    tmpl = prompt_lib.get("discussion")
    project_id = project["id"]

    console.rule("[bold yellow]Disagreement Review[/]")

    # Handle image vs text segments
    is_image = segment.get("media_type") == "image"
    images = [segment["image_path"]] if is_image and segment.get("image_path") else None

    if is_image:
        console.print(Panel(
            f"[Image segment: {segment.get('image_path', 'unknown')}]",
            title=f"[cyan]Segment {segment['id']} (Image)[/]",
        ))
        seg_text = f"[IMAGE: {segment.get('image_path', 'unknown')}] (image attached)"
    else:
        console.print(Panel(segment["text"], title=f"[cyan]Segment {segment['id']}[/]"))
        seg_text = segment["text"]

    console.print(f"  Agent A coded: [green]{', '.join(codes_a) or '(nothing)'}[/]")
    console.print(f"  Agent B coded: [yellow]{', '.join(codes_b) or '(nothing)'}[/]")
    if codes_c is not None:
        console.print(f"  Supervisor coded: [cyan]{', '.join(codes_c) or '(nothing)'}[/]")

    # Ask agents to explain their reasoning (if template available and agents are LLMs)
    explanation_a = ""
    explanation_b = ""

    if tmpl and hasattr(agent_a, '_call_llm'):
        system, user = tmpl.render(
            segment_text=seg_text,
            code_a=", ".join(codes_a) or "UNCODED",
            code_b=", ".join(codes_b) or "UNCODED",
            my_rationale=f"I assigned: {', '.join(codes_a) or 'nothing'}",
            their_rationale=f"Other agent assigned: {', '.join(codes_b) or 'nothing'}",
            agent_perspective="a",
        )
        _, parsed_a, call_id_a = agent_a.call("discussion", system, user, images=images)
        explanation_a = parsed_a.get("explanation", "")
        if explanation_a:
            console.print(Panel(explanation_a, title="[green]Agent A explains[/]", border_style="green"))

        # Save discussion turn
        turn_id = insert(conn, "discussion_turn", {
            "flag_id": flag_id,
            "agent_id": agent_a.agent_id,
            "turn_index": 1,
            "content": explanation_a,
            "llm_call_id": call_id_a,
        })

    if tmpl and hasattr(agent_b, '_call_llm'):
        system, user = tmpl.render(
            segment_text=seg_text,
            code_a=", ".join(codes_a) or "UNCODED",
            code_b=", ".join(codes_b) or "UNCODED",
            my_rationale=f"I assigned: {', '.join(codes_b) or 'nothing'}",
            their_rationale=explanation_a or f"Other agent assigned: {', '.join(codes_a) or 'nothing'}",
            agent_perspective="b",
        )
        _, parsed_b, call_id_b = agent_b.call("discussion", system, user, images=images)
        explanation_b = parsed_b.get("explanation", "")
        if explanation_b:
            console.print(Panel(explanation_b, title="[yellow]Agent B explains[/]", border_style="yellow"))

        insert(conn, "discussion_turn", {
            "flag_id": flag_id,
            "agent_id": agent_b.agent_id,
            "turn_index": 2,
            "content": explanation_b,
            "llm_call_id": call_id_b,
        })

    # Supervisor resolution
    console.print("\nResolution options:")
    console.print("  [green](a)[/] Accept Agent A's coding")
    console.print("  [yellow](b)[/] Accept Agent B's coding")
    console.print("  [cyan](m)[/] Accept both (multi-code)")
    console.print("  [red](x)[/] Reject both — enter correct code(s)")
    console.print("  [dim](d)[/] Defer to memo (document ambiguity)")

    choice = Prompt.ask("Resolution", choices=["a", "b", "m", "x", "d"], default="a")

    resolution_map = {
        "a": "accepted_a",
        "b": "accepted_b",
        "m": "merged",
        "x": "supervisor_override",
        "d": "deferred",
    }
    conn.commit()
    return resolution_map[choice]


# ─────────────────────────────────────────────────────────────────────────────
# Full calibration run
# ─────────────────────────────────────────────────────────────────────────────


def run_calibration(
    conn: sqlite3.Connection,
    project: dict,
    agent_a,
    agent_b,
    codebook_version_id: int,
    irr_threshold: float = DEFAULT_IRR_THRESHOLD,
    calibration_sample_size: int = 10,
    max_rounds: int = 5,
    include_supervisor: bool = False,
    supervisor_agent=None,
) -> dict:
    """
    Run calibration round(s) until IRR threshold is met or max_rounds reached.
    When include_supervisor=True, the supervisor codes as a third coder and
    3-way IRR is computed.
    Returns the final IRR results dict.
    """
    project_id = project["id"]
    three_way = include_supervisor and supervisor_agent is not None

    # Ensure calibration set is marked
    mark_calibration_set(conn, project_id, n=calibration_sample_size)

    for round_num in range(1, max_rounds + 1):
        console.print(f"\n[bold cyan]═══ Calibration Round {round_num} ═══[/]\n")

        # Run both agents on calibration set
        run_id_a = run_coding_session(
            conn, project, agent_a, codebook_version_id,
            run_type="calibration",
        )
        run_id_b = run_coding_session(
            conn, project, agent_b, codebook_version_id,
            run_type="calibration",
        )

        # Optionally run supervisor as third coder
        run_id_c = None
        if three_way:
            console.print("\n[bold cyan]Supervisor calibration coding:[/]")
            run_id_c = run_coding_session(
                conn, project, supervisor_agent, codebook_version_id,
                run_type="calibration",
            )

        # Compute IRR
        if three_way and run_id_c:
            irr_results = compute_irr_multiway(
                conn, project_id, [run_id_a, run_id_b, run_id_c],
                scope="calibration",
                notes=f"Calibration round {round_num} (3-way)",
            )
        else:
            irr_results = compute_irr(
                conn, project_id, run_id_a, run_id_b,
                scope="calibration",
                notes=f"Calibration round {round_num}",
            )
        print_irr_summary(irr_results)

        alpha = irr_results.get("krippendorff_alpha_3way") if three_way else irr_results["krippendorff_alpha"]
        if alpha is None:
            alpha = irr_results["krippendorff_alpha"]

        if not math.isnan(alpha) and alpha >= irr_threshold:
            console.print(
                f"\n[green]✓ IRR threshold met (α={alpha:.3f} ≥ {irr_threshold}).[/]\n"
                "Proceeding to full independent coding."
            )
            conn.execute(
                "UPDATE project SET status='coding', updated_at=datetime('now') WHERE id=?",
                (project_id,),
            )
            conn.commit()
            return irr_results

        console.print(
            f"\n[yellow]IRR below threshold (α={alpha:.3f} < {irr_threshold}).[/]"
        )

        if not Confirm.ask("Review disagreements and refine codebook?", default=True):
            break

        # Review disagreements
        if three_way and run_id_c:
            codes_maps, _ = get_coding_matrices(
                conn, [run_id_a, run_id_b, run_id_c], "calibration"
            )
            codes_a_map, codes_b_map, codes_c_map = codes_maps
            disagreements = find_disagreements_multiway([
                ("coder_a", codes_a_map),
                ("coder_b", codes_b_map),
                ("supervisor", codes_c_map),
            ])
        else:
            codes_a_map, codes_b_map, _ = get_coding_matrix(conn, run_id_a, run_id_b, "calibration")
            disagreements_2way = find_disagreements(codes_a_map, codes_b_map)
            # Normalize to multiway format for unified review
            disagreements = [
                {
                    "segment_id": d["segment_id"],
                    "codes_by_role": {"coder_a": d["codes_a"], "coder_b": d["codes_b"]},
                }
                for d in disagreements_2way
            ]
            codes_c_map = None

        console.print(f"\n{len(disagreements)} disagreements to review:\n")

        for d in disagreements:
            seg = fetchone(conn, "SELECT * FROM segment WHERE id = ?", (d["segment_id"],))
            if not seg:
                continue

            codes_by_role = d["codes_by_role"]
            d_codes_a = codes_by_role.get("coder_a", [])
            d_codes_b = codes_by_role.get("coder_b", [])
            d_codes_c = codes_by_role.get("supervisor")

            flag_desc = f"Calibration round {round_num}: A={d_codes_a}, B={d_codes_b}"
            if d_codes_c is not None:
                flag_desc += f", Sup={d_codes_c}"

            flag_id = insert(conn, "flag", {
                "project_id": project_id,
                "raised_by": agent_a.agent_id,
                "segment_id": d["segment_id"],
                "flag_type": "irr_disagreement",
                "description": flag_desc,
                "status": "in_discussion",
            })

            resolution = discuss_disagreement(
                conn, project, seg,
                d_codes_a, d_codes_b,
                agent_a, agent_b,
                flag_id=flag_id,
                codes_c=d_codes_c,
            )

            conn.execute(
                "UPDATE flag SET status='resolved', resolution=?, resolved_at=datetime('now') WHERE id=?",
                (resolution, flag_id),
            )
            conn.commit()

        if round_num < max_rounds:
            console.print(
                "\n[cyan]Based on the resolved disagreements, consider refining code "
                "definitions with `polyphony codebook edit <code-name>`.[/]"
            )
            if not Confirm.ask("Run another calibration round?", default=True):
                break

    console.print(
        "\n[yellow]Max calibration rounds reached. Proceeding with current codebook.[/]"
    )
    conn.execute(
        "UPDATE project SET status='coding', updated_at=datetime('now') WHERE id=?",
        (project_id,),
    )
    conn.commit()
    return irr_results
