"""
polyphony.pipeline.coding
====================
Independent coding pipeline.

Each agent codes every segment using the active codebook version.
Agents run sequentially (not in parallel) to keep SQLite writes simple
and avoid Ollama resource contention on a single GPU.

Independence is enforced: during a run, each agent's prompt contains only
the codebook and the target segment — never the other agent's assignments.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..db import fetchall, fetchone, insert, json_col
from ..prompts import library as prompt_lib, format_codebook

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Core segment coding function
# ─────────────────────────────────────────────────────────────────────────────


def code_segment(
    agent,
    segment: dict,
    codes: List[dict],
    project: dict,
    coding_run_id: int,
    conn: sqlite3.Connection,
    document_name: str = "unknown",
    total_segments: int = 1,
) -> List[dict]:
    """
    Have an agent code a single segment.
    Returns a list of assignment dicts with confidence, rationale, flags.
    Saves assignments to DB.
    """
    tmpl = prompt_lib["open_coding"]
    codebook_text = format_codebook(codes)

    research_questions = json.loads(project.get("research_questions") or "[]")
    rq_text = "\n".join(f"  - {q}" for q in research_questions) or "(not specified)"

    run_row = fetchone(conn, "SELECT codebook_version_id FROM coding_run WHERE id = ?",
                       (coding_run_id,))
    if not run_row or not run_row["codebook_version_id"]:
        raise ValueError(f"Coding run {coding_run_id} has no associated codebook version.")
    cb_row = fetchone(conn, "SELECT version FROM codebook_version WHERE id = ?",
                      (run_row["codebook_version_id"],))
    if not cb_row:
        raise ValueError(f"Codebook version {run_row['codebook_version_id']} not found.")

    system, user = tmpl.render(
        methodology=project["methodology"],
        research_question=rq_text,
        codebook_version=cb_row["version"],
        codebook_formatted=codebook_text,
        document_filename=document_name,
        segment_index=segment["segment_index"],
        total_segments=total_segments,
        segment_text=segment["text"],
    )

    raw, parsed, call_id = agent.call("coding", system, user)

    # Parse assignments from response
    assignments_raw = parsed.get("assignments", [])
    flags_raw = parsed.get("flags", [])
    saved_assignments = []

    code_name_to_id = {c["name"]: c["id"] for c in codes}

    for asgn in assignments_raw:
        code_name = asgn.get("code_name", "")
        if code_name == "UNCODED" or not code_name:
            continue
        code_id = code_name_to_id.get(code_name)
        if code_id is None:
            # Agent invented a code not in the codebook — flag it
            _raise_flag(conn, project["id"], agent.agent_id, segment["id"],
                        "missing_code", f"Agent used unknown code '{code_name}'")
            continue

        asgn_id = insert(conn, "assignment", {
            "coding_run_id": coding_run_id,
            "segment_id": segment["id"],
            "code_id": code_id,
            "agent_id": agent.agent_id,
            "confidence": asgn.get("confidence"),
            "rationale": asgn.get("rationale", ""),
            "is_primary": 1 if asgn.get("is_primary", True) else 0,
        })
        saved_assignments.append({"assignment_id": asgn_id, "code_name": code_name})

        # Link llm_call → assignment
        agent.update_call_link(call_id, assignment_id=asgn_id)

    # Handle flags raised by the agent
    for flag in flags_raw:
        _raise_flag(
            conn, project["id"], agent.agent_id, segment["id"],
            flag.get("flag_type", "ambiguous_segment"),
            flag.get("description", "Agent raised flag"),
        )

    conn.commit()
    return saved_assignments


def _raise_flag(
    conn: sqlite3.Connection,
    project_id: int,
    agent_id: int,
    segment_id: int,
    flag_type: str,
    description: str,
) -> int:
    return insert(conn, "flag", {
        "project_id": project_id,
        "raised_by": agent_id,
        "segment_id": segment_id,
        "flag_type": flag_type,
        "description": description,
        "status": "open",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Run a full coding session for one agent
# ─────────────────────────────────────────────────────────────────────────────


def run_coding_session(
    conn: sqlite3.Connection,
    project: dict,
    agent,
    codebook_version_id: int,
    run_type: str = "independent",
    segments: Optional[List[dict]] = None,
    resume: bool = False,
) -> int:
    """
    Run a full coding session (or resume an interrupted one).
    Returns the coding_run_id.

    If `segments` is None, all project segments are coded.
    If `resume` is True, already-coded segments are skipped.
    """
    project_id = project["id"]

    # Get or create coding_run
    # Check for any existing incomplete run first
    existing_run = fetchone(
        conn,
        """SELECT * FROM coding_run
           WHERE project_id = ? AND agent_id = ? AND run_type = ?
             AND codebook_version_id = ? AND status = 'running'
           ORDER BY id DESC LIMIT 1""",
        (project_id, agent.agent_id, run_type, codebook_version_id),
    )

    if resume:
        run = fetchone(
            conn,
            """SELECT * FROM coding_run
               WHERE project_id = ? AND agent_id = ? AND run_type = ?
                 AND codebook_version_id = ? AND status != 'complete'
               ORDER BY id DESC LIMIT 1""",
            (project_id, agent.agent_id, run_type, codebook_version_id),
        )
        if run:
            run_id = run["id"]
            console.print(f"[yellow]Resuming coding run {run_id}...[/]")
        else:
            run_id = _create_run(conn, project_id, agent, codebook_version_id, run_type)
    elif existing_run:
        console.print(
            f"[yellow]⚠ An incomplete coding run (id={existing_run['id']}) already exists "
            f"for {agent.role}. Use [bold]--resume[/bold] to continue it, or it will be "
            f"marked as cancelled and a new run started.[/]"
        )
        conn.execute(
            "UPDATE coding_run SET status='error', error_message='Superseded by new run' WHERE id=?",
            (existing_run["id"],),
        )
        conn.commit()
        run_id = _create_run(conn, project_id, agent, codebook_version_id, run_type)
    else:
        run_id = _create_run(conn, project_id, agent, codebook_version_id, run_type)

    # Load segments
    if segments is None:
        if run_type == "calibration":
            segments = fetchall(
                conn,
                "SELECT * FROM segment WHERE project_id = ? AND is_calibration = 1 ORDER BY id",
                (project_id,),
            )
        else:
            segments = fetchall(
                conn,
                "SELECT * FROM segment WHERE project_id = ? ORDER BY document_id, segment_index",
                (project_id,),
            )

    # Load codes
    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? AND is_active = 1 ORDER BY sort_order",
        (codebook_version_id,),
    )
    if not codes:
        raise ValueError(f"No active codes in codebook version {codebook_version_id}.")

    # Skip already-coded segments if resuming
    if resume:
        already_coded = {
            row["segment_id"]
            for row in fetchall(
                conn,
                "SELECT DISTINCT segment_id FROM assignment WHERE coding_run_id = ?",
                (run_id,),
            )
        }
        segments = [s for s in segments if s["id"] not in already_coded]
        console.print(f"  {len(already_coded)} segments already coded, "
                      f"{len(segments)} remaining.")

    if not segments:
        console.print("[green]All segments already coded.[/]")
        conn.execute(
            "UPDATE coding_run SET status='complete', completed_at=datetime('now') WHERE id=?",
            (run_id,),
        )
        conn.commit()
        return run_id

    # Load document names for display
    doc_map = {
        row["id"]: row["filename"]
        for row in fetchall(conn, "SELECT id, filename FROM document WHERE project_id = ?",
                            (project_id,))
    }

    console.print(f"\n[bold cyan]Coding {len(segments)} segments with {agent.info}[/]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"[{agent.role}]", total=len(segments))

        for seg in segments:
            doc_name = doc_map.get(seg["document_id"], "unknown")
            code_segment(
                agent=agent,
                segment=seg,
                codes=codes,
                project=project,
                coding_run_id=run_id,
                conn=conn,
                document_name=doc_name,
                total_segments=len(segments),
            )
            progress.advance(task)

    conn.execute(
        "UPDATE coding_run SET status='complete', completed_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    conn.commit()
    console.print(f"[green]✓ Coding complete. Run id={run_id}[/]")
    return run_id


def _create_run(conn, project_id, agent, codebook_version_id, run_type):
    run_id = insert(conn, "coding_run", {
        "project_id": project_id,
        "codebook_version_id": codebook_version_id,
        "agent_id": agent.agent_id,
        "run_type": run_type,
        "status": "running",
        "started_at": None,  # DB default
    })
    conn.commit()
    return run_id
