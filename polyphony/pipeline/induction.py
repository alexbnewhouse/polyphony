"""
polyphony.pipeline.induction
=======================
Codebook induction pipeline.

Workflow:
  1. Sample N segments from the corpus (stratified by document).
  2. Run each LLM agent on the sample (independently, different seeds).
  3. Collect candidate codes from both agents.
  4. Present merged, deduplicated list to human supervisor for review.
  5. Supervisor accepts/rejects/renames/merges candidates, adds criteria.
  6. Save finalized codebook version 1 to DB.
"""

from __future__ import annotations

import json
import random
import sqlite3
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..db import fetchall, fetchone, insert, json_col, update
from ..prompts import library as prompt_lib, format_codebook

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Sample selection
# ─────────────────────────────────────────────────────────────────────────────


def select_induction_sample(
    conn: sqlite3.Connection,
    project_id: int,
    n: int = 20,
    seed: int = 42,
) -> List[dict]:
    """
    Return a stratified random sample of segments for codebook induction.
    Stratified = proportional representation from each document.
    """
    docs = fetchall(
        conn,
        "SELECT DISTINCT document_id FROM segment WHERE project_id = ?",
        (project_id,),
    )
    if not docs:
        raise ValueError("No segments found. Import and segment documents first.")

    per_doc = max(1, n // len(docs))
    rng = random.Random(seed)
    sample = []
    for doc_row in docs:
        doc_segs = fetchall(
            conn,
            "SELECT * FROM segment WHERE project_id = ? AND document_id = ? ORDER BY segment_index",
            (project_id, doc_row["document_id"]),
        )
        chosen = rng.sample(doc_segs, min(per_doc, len(doc_segs)))
        sample.extend(chosen)

    # Trim to n if oversampled
    if len(sample) > n:
        rng.shuffle(sample)
        sample = sample[:n]

    return sorted(sample, key=lambda s: (s["document_id"], s["segment_index"]))


# ─────────────────────────────────────────────────────────────────────────────
# Agent induction run
# ─────────────────────────────────────────────────────────────────────────────


def run_agent_induction(
    agent,
    segments: List[dict],
    project: dict,
    coding_run_id: int,
    conn: sqlite3.Connection,
) -> List[dict]:
    """
    Run one agent's open-ended codebook induction over a sample of segments.
    Returns a list of candidate code dicts.
    """
    tmpl = prompt_lib["codebook_induction"]

    research_questions = json.loads(project.get("research_questions") or "[]")
    rq_text = "\n".join(f"  - {q}" for q in research_questions) or "  (not specified)"

    sample_text = "\n\n---\n\n".join(
        f"[Segment {s['segment_index']} from document {s['document_id']}]\n{s['text']}"
        for s in segments
    )

    system, user = tmpl.render(
        methodology=project["methodology"],
        research_questions=rq_text,
        sample_segments=sample_text,
        n_segments=len(segments),
    )

    console.print(f"  [dim]Running induction with {agent.info}...[/]")
    raw, parsed, call_id = agent.call("induction", system, user)

    # Update coding_run progress
    conn.execute(
        "UPDATE coding_run SET status = 'complete', completed_at = datetime('now') WHERE id = ?",
        (coding_run_id,),
    )
    conn.commit()

    candidates = parsed.get("codes", [])
    if not isinstance(candidates, list):
        console.print(f"  [yellow]Warning: unexpected response format from {agent.info}[/]")
        candidates = []

    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Merge candidates
# ─────────────────────────────────────────────────────────────────────────────


def merge_candidates(
    candidates_a: List[dict],
    candidates_b: List[dict],
) -> List[dict]:
    """
    Merge two lists of candidate codes, deduplicating by normalised name.
    Preserves both agents' descriptions when names differ.
    """
    merged: Dict[str, dict] = {}
    for code in candidates_a + candidates_b:
        name = code.get("name", "").strip()
        if not name:
            continue
        key = name.upper().replace(" ", "_").replace("-", "_")
        if key not in merged:
            merged[key] = {
                "name": name,
                "description": code.get("description", ""),
                "inclusion_criteria": code.get("inclusion_criteria", ""),
                "exclusion_criteria": code.get("exclusion_criteria", ""),
                "example_quotes": code.get("example_quotes", []),
                "level": code.get("level", "open"),
            }
        else:
            # If already present, enrich with additional description
            existing_desc = merged[key]["description"]
            new_desc = code.get("description", "")
            if new_desc and new_desc not in existing_desc:
                merged[key]["description"] = f"{existing_desc}; {new_desc}"

    return list(merged.values())


# ─────────────────────────────────────────────────────────────────────────────
# Human review
# ─────────────────────────────────────────────────────────────────────────────


def human_review_candidates(candidates: List[dict]) -> List[dict]:
    """
    Interactive terminal review of candidate codes.
    Supervisor can: accept (a), reject (r), rename (n), or edit description (e).
    Returns the approved list of code dicts.
    """
    console.print(
        Panel(
            f"[bold]{len(candidates)} candidate codes[/] generated by the two agents.\n"
            "Review each one: [green](a)[/]ccept, [red](r)[/]eject, "
            "[yellow](e)[/]dit, [cyan](n)[/]ew code, or [dim](s)[/]kip to accept all.",
            title="[bold cyan]Codebook Review[/]",
        )
    )

    approved = []
    i = 0
    while i < len(candidates):
        code = candidates[i]
        console.rule(f"Code {i + 1}/{len(candidates)}: [bold]{code['name']}[/]")
        console.print(f"Description: {code.get('description', '(none)')}")
        if code.get("inclusion_criteria"):
            console.print(f"Include if:  {code['inclusion_criteria']}")
        if code.get("exclusion_criteria"):
            console.print(f"Exclude if:  {code['exclusion_criteria']}")

        choice = Prompt.ask(
            "Action",
            choices=["a", "r", "e", "n", "s"],
            default="a",
        ).lower()

        if choice == "s":
            # Accept all remaining
            approved.extend(candidates[i:])
            break
        elif choice == "a":
            approved.append(code)
            i += 1
        elif choice == "r":
            console.print(f"[red]Rejected: {code['name']}[/]")
            i += 1
        elif choice == "e":
            code["name"] = Prompt.ask("Code name", default=code["name"])
            code["description"] = Prompt.ask("Description", default=code.get("description", ""))
            code["inclusion_criteria"] = Prompt.ask(
                "Include if (leave blank to skip)", default=code.get("inclusion_criteria", "")
            )
            code["exclusion_criteria"] = Prompt.ask(
                "Exclude if (leave blank to skip)", default=code.get("exclusion_criteria", "")
            )
            approved.append(code)
            i += 1
        elif choice == "n":
            new_code = {
                "name": Prompt.ask("New code name"),
                "description": Prompt.ask("Description"),
                "inclusion_criteria": Prompt.ask("Include if (leave blank to skip)", default=""),
                "exclusion_criteria": Prompt.ask("Exclude if (leave blank to skip)", default=""),
                "example_quotes": [],
                "level": "open",
            }
            approved.append(new_code)
            # Don't advance i — continue reviewing existing candidates

    # Ask if supervisor wants to add more codes
    while Confirm.ask("\nAdd another code manually?", default=False):
        new_code = {
            "name": Prompt.ask("Code name"),
            "description": Prompt.ask("Description"),
            "inclusion_criteria": Prompt.ask("Include if (leave blank to skip)", default=""),
            "exclusion_criteria": Prompt.ask("Exclude if (leave blank to skip)", default=""),
            "example_quotes": [],
            "level": Prompt.ask("Level", choices=["open", "axial", "selective"], default="open"),
        }
        approved.append(new_code)

    return approved


# ─────────────────────────────────────────────────────────────────────────────
# Save codebook to DB
# ─────────────────────────────────────────────────────────────────────────────


def save_codebook_version(
    conn: sqlite3.Connection,
    project_id: int,
    codes: List[dict],
    version: int,
    stage: str = "draft",
    rationale: str = "Initial induction",
    created_by: Optional[int] = None,
) -> int:
    """Save a new codebook version and its codes. Returns codebook_version_id."""
    cb_id = insert(
        conn,
        "codebook_version",
        {
            "project_id": project_id,
            "version": version,
            "stage": stage,
            "rationale": rationale,
            "created_by": created_by,
        },
    )
    for i, code in enumerate(codes):
        insert(
            conn,
            "code",
            {
                "project_id": project_id,
                "codebook_version_id": cb_id,
                "parent_id": None,
                "level": code.get("level", "open"),
                "name": code["name"],
                "description": code.get("description", ""),
                "inclusion_criteria": code.get("inclusion_criteria") or None,
                "exclusion_criteria": code.get("exclusion_criteria") or None,
                "example_quotes": json_col(code.get("example_quotes", [])),
                "is_active": 1,
                "sort_order": i,
            },
        )
    conn.commit()
    return cb_id


# ─────────────────────────────────────────────────────────────────────────────
# Full induction pipeline (orchestrator)
# ─────────────────────────────────────────────────────────────────────────────


def run_induction(
    conn: sqlite3.Connection,
    project: dict,
    agent_a,
    agent_b,
    sample_size: int = 20,
    sample_seed: int = 42,
    skip_agent_b: bool = False,
) -> int:
    """
    Full codebook induction pipeline. Returns new codebook_version_id.

    Steps:
      1. Sample segments
      2. Agent A induces candidate codes
      3. Agent B induces candidate codes (optional)
      4. Merge candidates
      5. Human reviews
      6. Save to DB
    """
    project_id = project["id"]

    console.print("\n[bold cyan]═══ Codebook Induction ═══[/]\n")

    # Step 1: Sample
    console.print(f"Selecting {sample_size} segments for induction...")
    segments = select_induction_sample(conn, project_id, n=sample_size, seed=sample_seed)
    console.print(f"  Sampled {len(segments)} segments from "
                  f"{len(set(s['document_id'] for s in segments))} documents.")

    # Step 2 & 3: Agent inductions (create coding_run records first)
    def _make_run(agent, run_type="induction"):
        cb = fetchone(
            conn,
            "SELECT id FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        )
        # If no codebook yet, we use a placeholder version 0 run
        cb_id = cb["id"] if cb else None
        if cb_id is None:
            # Create an empty placeholder codebook for the induction run
            cb_id = insert(conn, "codebook_version", {
                "project_id": project_id,
                "version": 0,
                "stage": "draft",
                "rationale": "Placeholder for induction run",
            })
            conn.commit()
        run_id = insert(conn, "coding_run", {
            "project_id": project_id,
            "codebook_version_id": cb_id,
            "agent_id": agent.agent_id,
            "run_type": "induction",
            "status": "running",
            "started_at": None,  # DB default
            "segment_count": len(segments),
        })
        conn.commit()
        return run_id

    run_id_a = _make_run(agent_a)
    candidates_a = run_agent_induction(agent_a, segments, project, run_id_a, conn)
    console.print(f"  Agent A proposed {len(candidates_a)} codes.")

    if not skip_agent_b:
        run_id_b = _make_run(agent_b)
        candidates_b = run_agent_induction(agent_b, segments, project, run_id_b, conn)
        console.print(f"  Agent B proposed {len(candidates_b)} codes.")
    else:
        candidates_b = []

    # Step 4: Merge
    all_candidates = merge_candidates(candidates_a, candidates_b)
    console.print(f"\nMerged to {len(all_candidates)} unique candidate codes.")

    # Step 5: Human review
    approved = human_review_candidates(all_candidates)
    console.print(f"\n[green]✓ {len(approved)} codes approved.[/]")

    # Step 6: Save
    # Find next version number
    existing = fetchone(
        conn,
        "SELECT MAX(version) as v FROM codebook_version WHERE project_id = ?",
        (project_id,),
    )
    next_version = (existing["v"] or 0) + 1

    cb_id = save_codebook_version(
        conn,
        project_id=project_id,
        codes=approved,
        version=next_version,
        stage="draft",
        rationale=f"Induction from {len(segments)}-segment sample",
        created_by=None,  # Supervisor synthesized it
    )

    # Advance project status
    conn.execute(
        "UPDATE project SET status = 'calibrating', updated_at = datetime('now') WHERE id = ?",
        (project_id,),
    )
    conn.commit()

    console.print(f"\n[green]Codebook v{next_version} saved (id={cb_id}).[/]")
    return cb_id
