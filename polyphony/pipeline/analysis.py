"""
polyphony.pipeline.analysis
======================
Post-coding analysis: theme synthesis, saturation, and pattern finding.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

from ..db import fetchall, fetchone
from ..prompts import library as prompt_lib, format_codebook

console = Console()


def code_frequency_table(
    conn: sqlite3.Connection,
    project_id: int,
    run_id: Optional[int] = None,
) -> List[dict]:
    """
    Return code frequencies across the corpus (or a specific run).
    Sorted descending by count.
    """
    query = """
        SELECT c.name AS code_name, c.level, c.description,
               COUNT(DISTINCT a.segment_id) AS segment_count,
               COUNT(a.id) AS assignment_count
        FROM assignment a
        JOIN code c ON c.id = a.code_id
        JOIN coding_run r ON r.id = a.coding_run_id
        WHERE r.project_id = ?
    """
    params: list = [project_id]
    if run_id:
        query += " AND a.coding_run_id = ?"
        params.append(run_id)
    query += " GROUP BY c.name ORDER BY segment_count DESC"
    return fetchall(conn, query, tuple(params))


def print_code_frequency(rows: List[dict]) -> None:
    table = Table(title="Code Frequency", show_header=True)
    table.add_column("Code", style="bold")
    table.add_column("Level")
    table.add_column("Segments", justify="right")
    table.add_column("Assignments", justify="right")
    for r in rows:
        table.add_row(r["code_name"], r["level"], str(r["segment_count"]), str(r["assignment_count"]))
    console.print(table)


def check_saturation(
    conn: sqlite3.Connection,
    project_id: int,
    window_size: int = 20,
) -> dict:
    """
    Theoretical saturation check: evaluates if novel codes are still emerging.
    
    Splits the segment coding timeline into windows of `window_size`.
    We consider the coding process "likely saturated" if the last three 
    consecutive windows produced zero new codes.
    """
    rows = fetchall(
        conn,
        """
        SELECT a.segment_id, c.name AS code_name, seg.segment_index,
               seg.document_id
        FROM assignment a
        JOIN code c ON c.id = a.code_id
        JOIN segment seg ON seg.id = a.segment_id
        JOIN coding_run r ON r.id = a.coding_run_id
        WHERE r.project_id = ? AND r.run_type = 'independent'
        ORDER BY seg.document_id, seg.segment_index
        """,
        (project_id,),
    )

    seen_codes: set = set()
    new_codes_per_window: List[int] = []
    window: List[int] = []

    for row in rows:
        if row["code_name"] not in seen_codes:
            seen_codes.add(row["code_name"])
            window.append(1)
        else:
            window.append(0)
        if len(window) >= window_size:
            new_codes_per_window.append(sum(window))
            window = []

    if window:
        new_codes_per_window.append(sum(window))

    return {
        "total_unique_codes": len(seen_codes),
        "new_codes_per_window": new_codes_per_window,
        "window_size": window_size,
        "likely_saturated": (
            len(new_codes_per_window) >= 3
            and all(n == 0 for n in new_codes_per_window[-3:])
        ),
    }


def co_occurrence_matrix(
    conn: sqlite3.Connection,
    project_id: int,
) -> Dict[str, Dict[str, int]]:
    """
    Build a code co-occurrence matrix: how often do two codes appear together
    in the same segment?
    Returns nested dict: {code_a: {code_b: count}}.
    Each pair is counted once per segment regardless of assignment count.
    """
    # Get all (segment_id, code_name) pairs
    rows = fetchall(
        conn,
        """
        SELECT DISTINCT a.segment_id, c.name AS code_name
        FROM assignment a
        JOIN code c ON c.id = a.code_id
        JOIN coding_run r ON r.id = a.coding_run_id
        WHERE r.project_id = ? AND r.run_type = 'independent'
        """,
        (project_id,),
    )

    # Use sets to guarantee each code appears at most once per segment
    by_seg: Dict[int, set] = {}
    for row in rows:
        by_seg.setdefault(row["segment_id"], set()).add(row["code_name"])

    matrix: Dict[str, Dict[str, int]] = {}
    for codes_set in by_seg.values():
        codes_list = sorted(codes_set)  # sorted for determinism
        for i, ca in enumerate(codes_list):
            for cb in codes_list[i + 1:]:
                row_a = matrix.setdefault(ca, {})
                row_a[cb] = row_a.get(cb, 0) + 1
                row_b = matrix.setdefault(cb, {})
                row_b[ca] = row_b.get(ca, 0) + 1

    return matrix


def synthesize_themes(
    agent: Any,
    conn: sqlite3.Connection,
    project: dict,
    codebook_version_id: int,
) -> str:
    """
    Use an LLM agent to synthesize themes from the coded corpus.
    Returns the agent's synthesis as a text string.
    """
    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? AND is_active = 1",
        (codebook_version_id,),
    )
    freq = code_frequency_table(conn, project["id"])
    freq_text = "\n".join(
        f"  {r['code_name']}: {r['segment_count']} segments" for r in freq[:20]
    )

    tmpl = prompt_lib.get("memo_synthesis")
    if not tmpl:
        return "(memo_synthesis prompt not found)"

    system, user = tmpl.render(
        codebook_formatted=format_codebook(codes),
        code_frequencies=freq_text,
        methodology=project["methodology"],
        research_questions=json.dumps(
            json.loads(project.get("research_questions") or "[]"), indent=2
        ),
        all_assignments="(see database)",
        related_memos="(see database)",
    )

    _, parsed, _ = agent.call("analysis", system, user)
    return parsed.get("synthesis", parsed.get("response", str(parsed)))


def code_frequency_by_document(
    conn: sqlite3.Connection,
    project_id: int,
    run_id: Optional[int] = None,
) -> List[dict]:
    """
    Return code frequencies broken down by document.

    Returns list of dicts with: document_id, filename, code_name, segment_count.
    Useful for comparing code distributions across podcast episodes.
    """
    query = """
        SELECT d.id AS document_id, d.filename,
               c.name AS code_name,
               COUNT(DISTINCT a.segment_id) AS segment_count
        FROM assignment a
        JOIN code c ON c.id = a.code_id
        JOIN segment seg ON seg.id = a.segment_id
        JOIN document d ON d.id = seg.document_id
        JOIN coding_run r ON r.id = a.coding_run_id
        WHERE r.project_id = ?
    """
    params: list = [project_id]
    if run_id:
        query += " AND a.coding_run_id = ?"
        params.append(run_id)
    query += " GROUP BY d.id, c.name ORDER BY d.filename, segment_count DESC"
    return fetchall(conn, query, tuple(params))


def speaker_frequency_table(
    conn: sqlite3.Connection,
    project_id: int,
    run_id: Optional[int] = None,
) -> List[dict]:
    """
    Return code frequencies broken down by speaker label.

    Only segments with a non-null speaker column are included.
    Useful for analyzing how different podcast speakers are coded.

    Returns list of dicts with: speaker, code_name, segment_count.
    """
    query = """
        SELECT seg.speaker,
               c.name AS code_name,
               COUNT(DISTINCT a.segment_id) AS segment_count
        FROM assignment a
        JOIN code c ON c.id = a.code_id
        JOIN segment seg ON seg.id = a.segment_id
        JOIN coding_run r ON r.id = a.coding_run_id
        WHERE r.project_id = ?
          AND seg.speaker IS NOT NULL
    """
    params: list = [project_id]
    if run_id:
        query += " AND a.coding_run_id = ?"
        params.append(run_id)
    query += " GROUP BY seg.speaker, c.name ORDER BY seg.speaker, segment_count DESC"
    return fetchall(conn, query, tuple(params))
