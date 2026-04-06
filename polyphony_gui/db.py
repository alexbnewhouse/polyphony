"""
polyphony_gui.db
================
Thin wrappers around the polyphony SQLite layer so every page can import
one consistent set of helpers instead of duplicating raw SQL.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from polyphony.db.connection import (
    connect,
    get_projects_root,
    project_db_path,
    fetchone,
    fetchall,
    insert,
    update,
    json_col,
    from_json,
)

logger = logging.getLogger("polyphony_gui")


# ─────────────────────────────────────────────────────────────────────────────
# Project helpers
# ─────────────────────────────────────────────────────────────────────────────


def list_projects() -> list[dict]:
    """Return all projects found in the configured projects root."""
    root = get_projects_root()
    projects = []
    if not root.exists():
        return projects
    for project_dir in sorted(root.iterdir()):
        db = project_dir / "project.db"
        if not db.exists():
            continue
        try:
            conn = connect(db)
            p = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
            conn.close()
            if p:
                p["db_path"] = str(db)
                projects.append(p)
        except Exception:
            continue
    return projects


def get_project_db(slug: str) -> Path:
    """Return the db_path for a given slug."""
    root = get_projects_root()
    return project_db_path(root, slug)


def load_project(db_path: str | Path) -> Optional[dict]:
    """Load the single project row from a db."""
    conn = connect(Path(db_path))
    p = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    conn.close()
    return p


def get_project_stats(db_path: str | Path, project_id: int) -> dict:
    conn = connect(Path(db_path))
    docs = fetchone(conn, "SELECT COUNT(*) AS n FROM document WHERE project_id = ?", (project_id,))["n"]
    segs = fetchone(conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?", (project_id,))["n"]
    codes = fetchone(conn, "SELECT COUNT(*) AS n FROM code WHERE project_id = ? AND is_active = 1", (project_id,))["n"]
    asgn = fetchone(conn, "SELECT COUNT(*) AS n FROM assignment WHERE coding_run_id IN (SELECT id FROM coding_run WHERE project_id = ?)", (project_id,))["n"]
    flags = fetchone(conn, "SELECT COUNT(*) AS n FROM flag WHERE project_id = ? AND status = 'open'", (project_id,))["n"]
    memos = fetchone(conn, "SELECT COUNT(*) AS n FROM memo WHERE project_id = ?", (project_id,))["n"]
    runs = fetchall(conn, "SELECT * FROM coding_run WHERE project_id = ? ORDER BY id DESC", (project_id,))
    irr_runs = fetchall(conn, "SELECT * FROM irr_run WHERE project_id = ? ORDER BY id DESC LIMIT 5", (project_id,))
    conn.close()
    return {
        "documents": docs,
        "segments": segs,
        "codes": codes,
        "assignments": asgn,
        "open_flags": flags,
        "memos": memos,
        "coding_runs": runs,
        "irr_runs": irr_runs,
    }


def create_project(
    name: str,
    description: str,
    methodology: str,
    research_questions: list[str],
    model_a: str,
    model_b: str,
    provider_a: str,
    provider_b: str,
    seed_a: int,
    seed_b: int,
    temperature: float,
) -> dict:
    """Create a new polyphony project and return the project row."""
    from polyphony.utils import slugify

    root = get_projects_root()
    slug = slugify(name)
    db_path = project_db_path(root, slug)

    if db_path.exists():
        raise ValueError(f"A project named '{slug}' already exists.")

    _PROVIDER_TO_AGENT_TYPE = {"ollama": "llm", "openai": "openai", "anthropic": "anthropic"}

    conn = connect(db_path)
    project_id = insert(conn, "project", {
        "name": name,
        "slug": slug,
        "description": description or None,
        "methodology": methodology,
        "research_questions": json_col(research_questions),
        "status": "setup",
        "config": "{}",
    })
    # Supervisor (human)
    insert(conn, "agent", {
        "project_id": project_id,
        "role": "supervisor",
        "agent_type": "human",
        "model_name": "human",
        "model_version": "human",
        "temperature": 0.0,
        "seed": 0,
        "system_prompt": "Human supervisor",
    })
    # Coder A
    insert(conn, "agent", {
        "project_id": project_id,
        "role": "coder_a",
        "agent_type": _PROVIDER_TO_AGENT_TYPE[provider_a],
        "model_name": model_a,
        "model_version": "unknown",
        "temperature": temperature,
        "seed": seed_a,
        "system_prompt": None,
    })
    # Coder B
    insert(conn, "agent", {
        "project_id": project_id,
        "role": "coder_b",
        "agent_type": _PROVIDER_TO_AGENT_TYPE[provider_b],
        "model_name": model_b,
        "model_version": "unknown",
        "temperature": temperature,
        "seed": seed_b,
        "system_prompt": None,
    })
    conn.commit()
    p = fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
    conn.close()
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Codebook helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_codebook(db_path: str | Path, project_id: int) -> Optional[dict]:
    """Return latest codebook version row, or None."""
    conn = connect(Path(db_path))
    cb = fetchone(
        conn,
        "SELECT * FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (project_id,),
    )
    conn.close()
    return cb


def get_codes(db_path: str | Path, codebook_version_id: int) -> list[dict]:
    conn = connect(Path(db_path))
    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? AND is_active = 1 ORDER BY sort_order, name",
        (codebook_version_id,),
    )
    conn.close()
    return codes


def save_codebook_from_candidates(
    db_path: str | Path,
    project_id: int,
    candidates: list[dict],
    rationale: str = "Inductively generated via GUI",
) -> int:
    """Save approved candidate codes as a new codebook version. Returns version_id."""
    conn = connect(Path(db_path))
    last_cb = fetchone(
        conn,
        "SELECT version FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (project_id,),
    )
    next_version = (last_cb["version"] + 1) if last_cb else 1

    cb_id = insert(conn, "codebook_version", {
        "project_id": project_id,
        "version": next_version,
        "stage": "draft",
        "rationale": rationale,
    })

    for i, code in enumerate(candidates):
        insert(conn, "code", {
            "project_id": project_id,
            "codebook_version_id": cb_id,
            "name": code.get("name", "").strip(),
            "description": code.get("description", ""),
            "inclusion_criteria": code.get("inclusion_criteria", ""),
            "exclusion_criteria": code.get("exclusion_criteria", ""),
            "example_quotes": json_col(code.get("example_quotes", [])),
            "level": code.get("level", "open"),
            "sort_order": i,
            "is_active": 1,
        })

    conn.execute(
        "UPDATE project SET status = 'inducing' WHERE id = ?", (project_id,)
    )
    conn.commit()
    conn.close()
    return cb_id


def update_project_status(db_path: str | Path, project_id: int, status: str) -> None:
    conn = connect(Path(db_path))
    conn.execute("UPDATE project SET status = ? WHERE id = ?", (status, project_id))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# IRR helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_irr_results(db_path: str | Path, project_id: int) -> list[dict]:
    conn = connect(Path(db_path))
    rows = fetchall(
        conn,
        "SELECT * FROM irr_run WHERE project_id = ? ORDER BY id DESC",
        (project_id,),
    )
    conn.close()
    return rows


def get_flags(db_path: str | Path, project_id: int, status: str = "open") -> list[dict]:
    conn = connect(Path(db_path))
    rows = fetchall(
        conn,
        """SELECT f.*, s.text AS segment_text
           FROM flag f
           LEFT JOIN segment s ON s.id = f.segment_id
           WHERE f.project_id = ? AND f.status = ?
           ORDER BY f.id""",
        (project_id, status),
    )
    conn.close()
    return rows


def resolve_flag(db_path: str | Path, flag_id: int, resolution: str) -> None:
    conn = connect(Path(db_path))
    conn.execute(
        "UPDATE flag SET status = 'resolved', resolution = ?, resolved_at = datetime('now') WHERE id = ?",
        (resolution, flag_id),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Memo helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_memos(db_path: str | Path, project_id: int) -> list[dict]:
    conn = connect(Path(db_path))
    rows = fetchall(conn, "SELECT * FROM memo WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
    conn.close()
    return rows


def add_memo(db_path: str | Path, project_id: int, title: str, content: str, memo_type: str = "analytic") -> None:
    """Add an analytical memo. Requires supervisor agent id."""
    conn = connect(Path(db_path))
    supervisor = fetchone(conn, "SELECT id FROM agent WHERE project_id = ? AND role = 'supervisor'", (project_id,))
    author_id = supervisor["id"] if supervisor else 1
    insert(conn, "memo", {
        "project_id": project_id,
        "author_id": author_id,
        "memo_type": memo_type,
        "title": title,
        "content": content,
        "linked_codes": "[]",
        "linked_segments": "[]",
        "linked_flags": "[]",
        "tags": "[]",
    })
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Document / segment helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_documents(db_path: str | Path, project_id: int) -> list[dict]:
    conn = connect(Path(db_path))
    rows = fetchall(conn, "SELECT * FROM document WHERE project_id = ? ORDER BY id", (project_id,))
    conn.close()
    return rows


def get_segments_preview(db_path: str | Path, project_id: int, limit: int = 20) -> list[dict]:
    conn = connect(Path(db_path))
    rows = fetchall(
        conn,
        "SELECT s.*, d.filename FROM segment s JOIN document d ON d.id = s.document_id "
        "WHERE s.project_id = ? ORDER BY s.document_id, s.segment_index LIMIT ?",
        (project_id, limit),
    )
    conn.close()
    return rows


def get_segment_count(db_path: str | Path, project_id: int) -> int:
    """Return total number of segments for a project."""
    conn = connect(Path(db_path))
    row = fetchone(conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?", (project_id,))
    conn.close()
    return row["n"] if row else 0


# ─────────────────────────────────────────────────────────────────────────────
# Coding run helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_coding_runs(db_path: str | Path, project_id: int) -> list[dict]:
    conn = connect(Path(db_path))
    rows = fetchall(
        conn,
        """SELECT r.*, a.role AS agent_role, a.model_name,
                  cb.version AS codebook_version
           FROM coding_run r
           JOIN agent a ON a.id = r.agent_id
           JOIN codebook_version cb ON cb.id = r.codebook_version_id
           WHERE r.project_id = ?
           ORDER BY r.id DESC""",
        (project_id,),
    )
    conn.close()
    return rows
