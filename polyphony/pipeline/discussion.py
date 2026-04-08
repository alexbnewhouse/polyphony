"""
polyphony.pipeline.discussion
========================
Flag resolution and discussion workflow.

Flags can be raised by:
  - LLM agents (during coding: ambiguous segments, missing codes)
  - IRR computation (disagreements)
  - Human supervisor (queries, concerns)

Resolution modes:
  - supervisor_override: Human decides unilaterally
  - agent_facilitated: Agents explain reasoning, human decides
  - deferred: Noted as genuine ambiguity (good for memos)
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..db import fetchall, fetchone, insert, update

console = Console()


def list_open_flags(
    conn: sqlite3.Connection,
    project_id: int,
    status: str = "open",
) -> List[dict]:
    """Return all flags with the given status, enriched with segment text."""
    query = """
        SELECT f.*, s.text AS segment_text, a.role AS raised_by_role
        FROM flag f
        LEFT JOIN segment s ON s.id = f.segment_id
        LEFT JOIN agent a ON a.id = f.raised_by
        WHERE f.project_id = ?
    """
    params = [project_id]
    if status != "all":
        query += " AND f.status = ?"
        params.append(status)
    query += " ORDER BY f.created_at DESC"
    return fetchall(conn, query, tuple(params))


def print_flags_table(flags: List[dict]) -> None:
    if not flags:
        console.print("[dim]No flags found.[/]")
        return

    table = Table(title=f"{len(flags)} Flag(s)", show_header=True)
    table.add_column("ID", width=5)
    table.add_column("Type", style="cyan")
    table.add_column("Raised by")
    table.add_column("Status")
    table.add_column("Description")

    status_colors = {
        "open": "yellow",
        "in_discussion": "blue",
        "resolved": "green",
        "deferred": "dim",
    }
    for f in flags:
        color = status_colors.get(f["status"], "white")
        table.add_row(
            str(f["id"]),
            f["flag_type"],
            f.get("raised_by_role", "?"),
            f"[{color}]{f['status']}[/]",
            (f["description"] or "")[:60] + ("..." if len(f.get("description", "")) > 60 else ""),
        )
    console.print(table)


def resolve_flag_interactive(
    conn: sqlite3.Connection,
    project: dict,
    flag_id: int,
    agent_a=None,
    agent_b=None,
    mode: str = "supervisor_override",
    blind_review: bool = False,
) -> None:
    """
    Interactively resolve a single flag.
    mode: 'supervisor_override' | 'agent_facilitated' | 'deferred'
    blind_review: if True, shuffle agent labels so supervisor can't tell which
        agent produced which codes (reduces anchoring on a "preferred" model).
    """
    flag = fetchone(conn, "SELECT * FROM flag WHERE id = ?", (flag_id,))
    if not flag:
        console.print(f"[red]Flag {flag_id} not found.[/]")
        return

    console.rule(f"[bold]Flag #{flag_id}: {flag['flag_type']}[/]")
    console.print(f"Raised by: agent {flag['raised_by']}")
    console.print(f"Status: {flag['status']}")
    console.print(f"Description: {flag['description']}")

    seg = None
    images = None
    if flag.get("segment_id"):
        seg = fetchone(conn, "SELECT * FROM segment WHERE id = ?", (flag["segment_id"],))
        if seg:
            if seg.get("media_type") == "image":
                console.print(Panel(
                    f"[Image segment: {seg.get('image_path', 'unknown')}]",
                    title="[cyan]Segment (Image)[/]",
                ))
                if seg.get("image_path"):
                    images = [seg["image_path"]]
            else:
                console.print(Panel(seg["text"], title="[cyan]Segment Text[/]"))

    if mode == "agent_facilitated" and agent_a and agent_b:
        # ── Blind review: optionally shuffle agent labels ────────────────
        import random

        agents_ordered = [(agent_a, "A"), (agent_b, "B")]
        if blind_review:
            random.shuffle(agents_ordered)
            console.print("[dim]Blind review mode: agent labels randomised.[/]")

        # Map agents by stable agent_id (not Python object id)
        agent_codes_map: dict[int, list] = {}

        # ── Commit-then-reveal: ask human to assess before showing agent work ──
        console.print(
            "\n[bold]Before seeing the agents' codes, how would you code this segment?[/]"
        )
        blind_code = Prompt.ask(
            "Your code(s) (comma-separated, or 'skip' to skip)",
            default="skip",
        )
        if blind_code.strip().lower() != "skip":
            console.print(f"  [dim]Recorded your blind assessment: {blind_code}[/]")
            conn.execute(
                "UPDATE flag SET supervisor_blind_code = ? WHERE id = ?",
                (blind_code, flag_id),
            )
            conn.commit()

        from ..prompts import library as prompt_lib
        tmpl = prompt_lib.get("discussion")
        if tmpl:
            # Get most recent assignments for this segment from each agent
            def get_codes(agent):
                rows = fetchall(
                    conn,
                    """SELECT c.name FROM assignment a
                       JOIN code c ON c.id = a.code_id
                       JOIN coding_run r ON r.id = a.coding_run_id
                       WHERE a.segment_id = ? AND r.agent_id = ?
                       ORDER BY a.created_at DESC""",
                    (flag["segment_id"], agent.agent_id),
                )
                return [r["name"] for r in rows]

            codes_a = get_codes(agent_a)
            codes_b = get_codes(agent_b)

            # Map agents by stable agent_id
            agent_codes_map[agent_a.agent_id] = codes_a
            agent_codes_map[agent_b.agent_id] = codes_b

            for idx, (agent, display_label) in enumerate(agents_ordered):
                if not hasattr(agent, '_call_llm'):
                    continue
                codes_self = agent_codes_map[agent.agent_id]
                other_agent = agents_ordered[1 - idx][0]
                codes_other = agent_codes_map[other_agent.agent_id]
                perspective = "a" if agent is agent_a else "b"
                seg_text = seg["text"] if seg else "(no segment)"
                if seg and seg.get("media_type") == "image":
                    seg_text = f"[IMAGE: {seg.get('image_path', 'unknown')}] (image attached)"
                system, user = tmpl.render(
                    segment_text=seg_text,
                    code_a=", ".join(codes_a) or "UNCODED",
                    code_b=", ".join(codes_b) or "UNCODED",
                    my_rationale=f"My codes: {', '.join(codes_self) or 'none'}",
                    their_rationale=f"Other agent's codes: {', '.join(codes_other) or 'none'}",
                    agent_perspective=perspective,
                )
                _, parsed, call_id = agent.call("discussion", system, user, images=images)
                explanation = parsed.get("explanation", "")
                if explanation:
                    # In blind mode, show "Coder 1"/"Coder 2" instead of "Agent A"/"Agent B"
                    if blind_review:
                        title = f"[bold]Coder {idx + 1} explains[/]"
                    else:
                        title = f"[bold]Agent {display_label} explains[/]"
                    console.print(Panel(
                        explanation,
                        title=title,
                        border_style="green" if idx == 0 else "yellow",
                    ))
                    insert(conn, "discussion_turn", {
                        "flag_id": flag_id,
                        "agent_id": agent.agent_id,
                        "turn_index": 1 if perspective == "a" else 2,
                        "content": explanation,
                        "llm_call_id": call_id,
                    })

    # Supervisor decision
    if mode == "deferred":
        resolution = "deferred"
        console.print("[dim]Flagged as deferred (genuine ambiguity noted).[/]")
    else:
        resolution = Prompt.ask(
            "Enter resolution or decision",
            default="Supervisor reviewed and resolved."
        )

    new_status = "deferred" if mode == "deferred" else "resolved"
    conn.execute(
        """UPDATE flag SET status = ?, resolution = ?, resolved_at = datetime('now')
           WHERE id = ?""",
        (new_status, resolution, flag_id),
    )
    conn.commit()
    console.print(f"[green]✓ Flag {flag_id} {new_status}.[/]")


def raise_flag(
    conn: sqlite3.Connection,
    project_id: int,
    agent_id: int,
    flag_type: str,
    description: str,
    segment_id: Optional[int] = None,
    code_id: Optional[int] = None,
) -> int:
    """Programmatically raise a flag. Returns the new flag_id."""
    flag_id = insert(conn, "flag", {
        "project_id": project_id,
        "raised_by": agent_id,
        "segment_id": segment_id,
        "code_id": code_id,
        "flag_type": flag_type,
        "description": description,
        "status": "open",
    })
    conn.commit()
    return flag_id
