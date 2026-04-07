"""
polyphony.pipeline.induction
=======================
Codebook induction pipeline.

Workflow:
  1. Sample N segments from the corpus (stratified by document).
  2. Run each LLM agent on the sample (in parallel, different seeds).
  3. Collect candidate codes from both agents.
  4. Present merged, deduplicated list to human supervisor for review.
  5. Supervisor accepts/rejects/renames/merges candidates, adds criteria.
  6. Save finalized codebook version 1 to DB.
"""

from __future__ import annotations

import json
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..db import fetchall, fetchone, insert, json_col, update
from ..db.connection import connect as db_connect
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

    sample_parts = []
    image_paths = []
    for s in segments:
        if s.get("media_type") == "image":
            sample_parts.append(
                f"[Segment {s['segment_index']} from document {s['document_id']}]\n"
                f"[IMAGE: see attached image #{len(image_paths) + 1}]"
            )
            if s.get("image_path"):
                image_paths.append(s["image_path"])
        else:
            sample_parts.append(
                f"[Segment {s['segment_index']} from document {s['document_id']}]\n{s['text']}"
            )
    sample_text = "\n\n---\n\n".join(sample_parts)

    system, user = tmpl.render(
        methodology=project["methodology"],
        research_questions=rq_text,
        sample_segments=sample_text,
        n_segments=len(segments),
    )

    console.print(f"  [dim]Running induction with {agent.info}...[/]")
    images = image_paths if image_paths else None
    raw, parsed, call_id = agent.call("induction", system, user, images=images)

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


def run_human_induction(
    conn: sqlite3.Connection,
    project: dict,
    supervisor_agent,
    segments: List[dict],
) -> List[dict]:
    """
    Interactive code proposal by the human researcher.
    Uses the supervisor_agent's propose_codes() method.
    Returns a list of candidate code dicts.
    """
    console.print("\n[bold cyan]Human-Led Codebook Induction[/]")
    console.print(f"  You will review {len(segments)} sample segments and propose codes.\n")
    return supervisor_agent.propose_codes(segments)


def merge_candidates(
    *candidate_lists: List[dict],
) -> List[dict]:
    """
    Merge N lists of candidate codes, deduplicating by normalised name.
    Enriches descriptions when multiple agents proposed similar codes.
    """
    merged: Dict[str, dict] = {}
    all_candidates = []
    for cl in candidate_lists:
        all_candidates.extend(cl)
    for code in all_candidates:
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
# Referee deduplication
# ─────────────────────────────────────────────────────────────────────────────


def _format_candidates_for_referee(candidates: List[dict]) -> str:
    """Format candidate codes as a numbered list for the referee prompt."""
    parts = []
    for i, code in enumerate(candidates, 1):
        lines = [f"{i}. **{code.get('name', 'UNNAMED')}**"]
        if code.get("description"):
            lines.append(f"   Description: {code['description']}")
        if code.get("inclusion_criteria"):
            lines.append(f"   Include when: {code['inclusion_criteria']}")
        if code.get("exclusion_criteria"):
            lines.append(f"   Exclude when: {code['exclusion_criteria']}")
        quotes = code.get("example_quotes", [])
        if quotes:
            lines.append(f"   Examples: {'; '.join(str(q) for q in quotes[:2])}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def referee_dedup_candidates(
    agent,
    candidates: List[dict],
    project: dict,
    conn: sqlite3.Connection,
) -> List[dict]:
    """
    Run a referee/deduplication pass on merged candidate codes.

    Uses a third model (or one of the existing coders) to identify
    near-duplicate, overlapping, or redundant codes and annotate each
    candidate with:
      - _referee_verdict: "keep", "merge", or "discard"
      - _referee_confidence: 0.0-1.0
      - _referee_reason: explanation
      - _referee_merge_into: primary code name (for merges)
      - _referee_merged_name: suggested merged name
      - _referee_merged_description: combined description

    Returns the candidates list with referee annotations added.
    """
    tmpl = prompt_lib["codebook_referee"]

    research_questions = json.loads(project.get("research_questions") or "[]")
    rq_text = "\n".join(f"  - {q}" for q in research_questions) or "  (not specified)"

    candidate_text = _format_candidates_for_referee(candidates)

    system, user = tmpl.render(
        methodology=project["methodology"],
        research_questions=rq_text,
        n_codes=len(candidates),
        candidate_codes_formatted=candidate_text,
    )

    console.print(f"  [dim]Running referee dedup with {agent.info}...[/]")

    # Create a coding_run record for auditability
    from ..db import fetchone as db_fetchone, insert as db_insert
    cb = db_fetchone(
        conn,
        "SELECT id FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (project["id"],),
    )
    cb_id = cb["id"] if cb else None
    if cb_id is None:
        cb_id = db_insert(conn, "codebook_version", {
            "project_id": project["id"],
            "version": 0,
            "stage": "draft",
            "rationale": "Placeholder for referee run",
        })
        conn.commit()

    run_id = db_insert(conn, "coding_run", {
        "project_id": project["id"],
        "codebook_version_id": cb_id,
        "agent_id": agent.agent_id,
        "run_type": "referee",
        "status": "running",
        "started_at": None,
        "segment_count": 0,
    })
    conn.commit()

    raw, parsed, call_id = agent.call("referee", system, user)

    conn.execute(
        "UPDATE coding_run SET status = 'complete', completed_at = datetime('now') WHERE id = ?",
        (run_id,),
    )
    conn.commit()

    # Apply referee annotations to candidates
    reviews = parsed.get("reviews", [])
    review_map = {}
    for r in reviews:
        key = r.get("code_name", "").strip().upper().replace(" ", "_").replace("-", "_")
        review_map[key] = r

    duplicate_groups = parsed.get("duplicate_groups", [])

    for code in candidates:
        key = code.get("name", "").strip().upper().replace(" ", "_").replace("-", "_")
        review = review_map.get(key, {})
        code["_referee_verdict"] = review.get("verdict", "keep")
        code["_referee_confidence"] = review.get("confidence", 0.5)
        code["_referee_reason"] = review.get("reason", "")
        code["_referee_merge_into"] = review.get("merge_into")
        code["_referee_merged_name"] = review.get("merged_name")
        code["_referee_merged_description"] = review.get("merged_description")

    return candidates, duplicate_groups, parsed.get("summary", "")


def apply_referee_recommendations(
    candidates: List[dict],
    duplicate_groups: List[dict],
    auto_apply: bool = False,
) -> List[dict]:
    """
    Apply referee recommendations to produce a deduplicated candidate list.

    If auto_apply=True, automatically merges/discards as recommended.
    Otherwise, just sorts candidates so that "keep" codes come first,
    then "merge", then "discard" — leaving the final decision to the human.

    Returns the processed candidate list (with referee metadata intact).
    """
    if not auto_apply:
        # Sort: keep first, merge second, discard last
        verdict_order = {"keep": 0, "merge": 1, "discard": 2}
        candidates.sort(
            key=lambda c: (
                verdict_order.get(c.get("_referee_verdict", "keep"), 3),
                -c.get("_referee_confidence", 0.5),
            )
        )
        return candidates

    # Auto-apply: merge groups and discard redundant codes
    # Build a set of codes that are being merged away
    merged_away = set()
    merge_replacements = {}  # code_name -> replacement dict

    for group in duplicate_groups:
        codes_in_group = [c.upper().replace(" ", "_").replace("-", "_") for c in group.get("codes", [])]
        recommended_name = group.get("recommended_name", codes_in_group[0] if codes_in_group else "")
        recommended_desc = group.get("recommended_description", "")

        # The first code in the group becomes the primary
        for code_name in codes_in_group[1:]:
            merged_away.add(code_name)
        if codes_in_group:
            merge_replacements[codes_in_group[0]] = {
                "name": recommended_name,
                "description": recommended_desc,
            }

    result = []
    for code in candidates:
        key = code.get("name", "").strip().upper().replace(" ", "_").replace("-", "_")
        verdict = code.get("_referee_verdict", "keep")

        if verdict == "discard" or key in merged_away:
            continue

        # Apply merge name/description if this is a primary in a merge group
        if key in merge_replacements:
            replacement = merge_replacements[key]
            if replacement.get("name"):
                code["name"] = replacement["name"]
            if replacement.get("description"):
                code["description"] = replacement["description"]

        result.append(code)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Human review
# ─────────────────────────────────────────────────────────────────────────────


def human_review_candidates(candidates: List[dict], auto_accept_all: bool = False) -> List[dict]:
    """
    Interactive terminal review of candidate codes.
    Supervisor can: accept (a), reject (r), rename (n), or edit description (e).
    Returns the approved list of code dicts.
    """
    if auto_accept_all:
        console.print(
            f"[yellow]Auto-accept enabled: approving all {len(candidates)} candidate codes without interactive review.[/]"
        )
        return list(candidates)

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
        # Show referee recommendation if available
        verdict = code.get("_referee_verdict")
        confidence = code.get("_referee_confidence")
        reason = code.get("_referee_reason", "")

        verdict_display = ""
        if verdict:
            verdict_colors = {"keep": "green", "merge": "yellow", "discard": "red"}
            color = verdict_colors.get(verdict, "dim")
            conf_pct = f"{confidence * 100:.0f}%" if confidence is not None else "?"
            verdict_display = f"  [{color}]Referee: {verdict.upper()} ({conf_pct} confidence)[/{color}]"
            if reason:
                verdict_display += f"\n  [dim]{reason}[/]"
            if verdict == "merge" and code.get("_referee_merge_into"):
                verdict_display += f"\n  [yellow]→ Merge into: {code['_referee_merge_into']}[/]"
                if code.get("_referee_merged_name"):
                    verdict_display += f" as [bold]{code['_referee_merged_name']}[/]"

        console.rule(f"Code {i + 1}/{len(candidates)}: [bold]{code['name']}[/]")
        if verdict_display:
            console.print(verdict_display)
        console.print(f"Description: {code.get('description', '(none)')}")
        if code.get("inclusion_criteria"):
            console.print(f"Include if:  {code['inclusion_criteria']}")
        if code.get("exclusion_criteria"):
            console.print(f"Exclude if:  {code['exclusion_criteria']}")

        # Default action based on referee verdict
        default_action = "a"
        if verdict == "discard":
            default_action = "r"

        choice = Prompt.ask(
            "Action",
            choices=["a", "r", "e", "n", "s"],
            default=default_action,
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
    human_leads: bool = False,
    supervisor_agent=None,
    auto_accept_all: bool = False,
    referee_agent=None,
    skip_referee: bool = False,
) -> int:
    """
    Full codebook induction pipeline. Returns new codebook_version_id.

    Steps:
      1. Sample segments
      2. (If human_leads) Human proposes codes first
      3. Agent A induces candidate codes
      4. Agent B induces candidate codes (optional)
      5. Merge candidates
      6. (Optional) Referee deduplication pass
      7. Human reviews
      8. Save to DB

    referee_agent: an agent to use for the dedup/referee pass. If None and
        skip_referee is False, uses agent_b (or agent_a if agent_b was skipped).
    skip_referee: if True, skip the referee dedup pass entirely.
    """
    project_id = project["id"]

    console.print("\n[bold cyan]═══ Codebook Induction ═══[/]\n")

    # Step 1: Sample
    console.print(f"Selecting {sample_size} segments for induction...")
    segments = select_induction_sample(conn, project_id, n=sample_size, seed=sample_seed)
    n_docs_sampled = len(set(s['document_id'] for s in segments))
    if len(segments) < sample_size:
        console.print(
            f"  [yellow]Sampled {len(segments)} segments from {n_docs_sampled} document(s) "
            f"(requested {sample_size}, but only {len(segments)} available in corpus).[/]"
        )
    else:
        console.print(
            f"  Sampled {len(segments)} segments from {n_docs_sampled} document(s)."
        )

    # Step 2 (optional): Human-led induction
    candidates_human = []
    if human_leads and supervisor_agent is not None:
        candidates_human = run_human_induction(conn, project, supervisor_agent, segments)
        console.print(f"  Human proposed {len(candidates_human)} codes.")

    # Step 3 & 4: Agent inductions (create coding_run records first)
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

    if not skip_agent_b:
        run_id_b = _make_run(agent_b)

    # Derive db_path for per-thread connections
    _db_path = conn.execute("PRAGMA database_list").fetchone()["file"]

    def _induct(agent, run_id):
        thread_conn = db_connect(Path(_db_path))
        orig_conn = agent.conn
        agent.conn = thread_conn
        try:
            return run_agent_induction(agent, segments, project, run_id, thread_conn)
        finally:
            agent.conn = orig_conn
            thread_conn.close()

    if not skip_agent_b:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(_induct, agent_a, run_id_a)
            future_b = executor.submit(_induct, agent_b, run_id_b)
            candidates_a = future_a.result()
            candidates_b = future_b.result()
        console.print(f"  Agent A proposed {len(candidates_a)} codes.")
        console.print(f"  Agent B proposed {len(candidates_b)} codes.")
    else:
        candidates_a = _induct(agent_a, run_id_a)
        console.print(f"  Agent A proposed {len(candidates_a)} codes.")
        candidates_b = []

    # Step 5: Merge all candidate lists
    all_candidate_lists = []
    if candidates_human:
        all_candidate_lists.append(candidates_human)
    all_candidate_lists.append(candidates_a)
    if candidates_b:
        all_candidate_lists.append(candidates_b)
    all_candidates = merge_candidates(*all_candidate_lists)
    console.print(f"\nMerged to {len(all_candidates)} unique candidate codes.")

    # Step 6 (optional): Referee deduplication pass
    if not skip_referee and len(all_candidates) > 1:
        ref_agent = referee_agent or (agent_b if not skip_agent_b else agent_a)
        console.print(f"\n[bold cyan]Running referee dedup pass...[/]")
        try:
            all_candidates, dup_groups, referee_summary = referee_dedup_candidates(
                ref_agent, all_candidates, project, conn,
            )
            # Display referee summary
            keep_count = sum(1 for c in all_candidates if c.get("_referee_verdict") == "keep")
            merge_count = sum(1 for c in all_candidates if c.get("_referee_verdict") == "merge")
            discard_count = sum(1 for c in all_candidates if c.get("_referee_verdict") == "discard")

            console.print()
            ref_table = Table(title="🔍 Referee Deduplication Results", border_style="magenta")
            ref_table.add_column("Verdict", style="bold")
            ref_table.add_column("Count")
            ref_table.add_row("[green]Keep[/]", str(keep_count))
            ref_table.add_row("[yellow]Merge[/]", str(merge_count))
            ref_table.add_row("[red]Discard[/]", str(discard_count))
            console.print(ref_table)

            if dup_groups:
                console.print("\n[bold]Duplicate groups identified:[/]")
                for g in dup_groups:
                    codes_str = ", ".join(g.get("codes", []))
                    rec = g.get("recommended_name", "?")
                    console.print(f"  [yellow]→[/] {codes_str}  ⟶  [green]{rec}[/]")

            if referee_summary:
                console.print(f"\n  [dim]Referee: {referee_summary}[/]")

            # Sort candidates: keep first, then merge, then discard
            all_candidates = apply_referee_recommendations(all_candidates, dup_groups, auto_apply=False)

        except Exception as e:
            console.print(f"\n  [yellow]⚠ Referee pass failed: {e}[/]")
            console.print("  [dim]Continuing with unreviewed candidates.[/]")

    # Step 7: Human review
    approved = human_review_candidates(all_candidates, auto_accept_all=auto_accept_all)
    if not approved:
        raise ValueError("No codes were approved during induction.")
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
