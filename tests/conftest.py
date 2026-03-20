"""
Test fixtures for polyphony.

These fixtures create a fully-populated in-memory (or temp-file) database
so tests can run without Ollama or real data files.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from polyphony.db import connect, insert, json_col


# ─────────────────────────────────────────────────────────────────────────────
# Sample data
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_SEGMENTS = [
    "I can't make ends meet anymore. The rent went up again and I don't know how I'll pay it next month.",
    "My landlord hasn't fixed the heating in three months. We're sleeping in coats. It's not right.",
    "I've been working two jobs but still not enough. My kids ask for things and I have to say no.",
    "The food bank has been a lifeline. Without it I don't know what we'd do.",
    "I feel ashamed going to the food bank but I have no choice. My pride took a hit.",
    "My friends don't really understand what I'm going through. I feel very alone in all of this.",
    "I stopped going out because I can't afford it. My social life is gone.",
    "The stress of not knowing if you can pay the bills affects your health. I can't sleep.",
    "My employer cut my hours again. No warning, nothing. Just a text message.",
    "I had to choose between food and electricity. Nobody should have to make that choice.",
]

SAMPLE_CODES = [
    {
        "name": "FINANCIAL_STRESS",
        "description": "Participant expresses worry or distress about money, debt, or finances",
        "inclusion_criteria": "Direct references to financial anxiety, inability to pay bills",
        "exclusion_criteria": "General dissatisfaction without financial component",
        "example_quotes": ["I can't make ends meet"],
        "level": "open",
    },
    {
        "name": "HOUSING_PRECARITY",
        "description": "Participant describes unstable or inadequate housing conditions",
        "inclusion_criteria": "Rent increases, poor conditions, fear of eviction",
        "exclusion_criteria": "General housing preferences",
        "example_quotes": ["The rent went up again"],
        "level": "open",
    },
    {
        "name": "SOCIAL_ISOLATION",
        "description": "Participant describes feeling cut off from social networks",
        "inclusion_criteria": "Reduced social contact, loneliness, feeling misunderstood",
        "exclusion_criteria": "",
        "example_quotes": ["I feel very alone"],
        "level": "open",
    },
    {
        "name": "COPING_STRATEGY",
        "description": "Participant describes ways of managing precarious circumstances",
        "inclusion_criteria": "Food banks, multiple jobs, cutting expenses",
        "exclusion_criteria": "",
        "example_quotes": ["The food bank has been a lifeline"],
        "level": "open",
    },
    {
        "name": "SHAME_STIGMA",
        "description": "Participant expresses shame, embarrassment, or stigma",
        "inclusion_criteria": "Pride, shame, embarrassment related to poverty",
        "exclusion_criteria": "",
        "example_quotes": ["I feel ashamed going to the food bank"],
        "level": "open",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path) -> Path:
    """Return a path to a temporary polyphony database."""
    return tmp_path / "test_project.db"


@pytest.fixture
def conn(db_path) -> sqlite3.Connection:
    """Return an open connection to a fresh test database."""
    c = connect(db_path)
    yield c
    c.close()


@pytest.fixture
def project_id(conn) -> int:
    """Create a minimal project and return its ID."""
    pid = insert(conn, "project", {
        "name": "Test Project",
        "slug": "test-project",
        "description": "A test project for housing precarity research",
        "methodology": "grounded_theory",
        "research_questions": json_col(["How do residents experience housing precarity?"]),
        "status": "coding",
        "config": "{}",
    })
    # Supervisor
    insert(conn, "agent", {
        "project_id": pid,
        "role": "supervisor",
        "agent_type": "human",
        "model_name": "human",
        "model_version": "human",
        "temperature": 0.0,
        "seed": 0,
    })
    # Coder A
    insert(conn, "agent", {
        "project_id": pid,
        "role": "coder_a",
        "agent_type": "llm",
        "model_name": "llama3.1:8b",
        "model_version": "sha256:test_digest_a",
        "temperature": 0.1,
        "seed": 42,
    })
    # Coder B
    insert(conn, "agent", {
        "project_id": pid,
        "role": "coder_b",
        "agent_type": "llm",
        "model_name": "llama3.1:8b",
        "model_version": "sha256:test_digest_b",
        "temperature": 0.1,
        "seed": 137,
    })
    conn.commit()
    return pid


@pytest.fixture
def document_id(conn, project_id) -> int:
    """Create a test document and return its ID."""
    from polyphony.io.importers import sha256
    content = "\n\n".join(SAMPLE_SEGMENTS)
    doc_id = insert(conn, "document", {
        "project_id": project_id,
        "filename": "test_interview.txt",
        "content": content,
        "content_hash": sha256(content),
        "char_count": len(content),
        "word_count": len(content.split()),
        "status": "segmented",
        "metadata": "{}",
    })
    # Insert segments
    for i, text in enumerate(SAMPLE_SEGMENTS):
        insert(conn, "segment", {
            "document_id": doc_id,
            "project_id": project_id,
            "segment_index": i,
            "text": text,
            "char_start": i * 100,
            "char_end": i * 100 + len(text),
            "segment_hash": sha256(text),
            "is_calibration": 1 if i < 3 else 0,
        })
    conn.commit()
    return doc_id


@pytest.fixture
def codebook_version_id(conn, project_id) -> int:
    """Create a test codebook version and return its ID."""
    cb_id = insert(conn, "codebook_version", {
        "project_id": project_id,
        "version": 1,
        "stage": "draft",
        "rationale": "Test codebook",
    })
    for i, code in enumerate(SAMPLE_CODES):
        insert(conn, "code", {
            "project_id": project_id,
            "codebook_version_id": cb_id,
            "level": code["level"],
            "name": code["name"],
            "description": code["description"],
            "inclusion_criteria": code["inclusion_criteria"],
            "exclusion_criteria": code["exclusion_criteria"],
            "example_quotes": json_col(code["example_quotes"]),
            "is_active": 1,
            "sort_order": i,
        })
    conn.commit()
    return cb_id


@pytest.fixture
def coding_run_ids(conn, project_id, document_id, codebook_version_id):
    """
    Create two completed coding runs (A and B) with simulated assignments.
    Returns (run_id_a, run_id_b).
    """
    from polyphony.db import fetchall, fetchone

    agents = {
        row["role"]: row
        for row in fetchall(conn, "SELECT * FROM agent WHERE project_id = ?", (project_id,))
    }
    segments = fetchall(conn, "SELECT * FROM segment WHERE project_id = ?", (project_id,))
    codes = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ?", (codebook_version_id,))
    code_by_name = {c["name"]: c["id"] for c in codes}

    def make_run(agent_role: str) -> int:
        run_id = insert(conn, "coding_run", {
            "project_id": project_id,
            "codebook_version_id": codebook_version_id,
            "agent_id": agents[agent_role]["id"],
            "run_type": "independent",
            "status": "complete",
            "segment_count": len(segments),
        })
        return run_id

    # Simulated coding: A and B mostly agree, disagree on 2 segments
    assignments_a = {
        0: ["FINANCIAL_STRESS"],
        1: ["HOUSING_PRECARITY"],
        2: ["FINANCIAL_STRESS"],
        3: ["COPING_STRATEGY"],
        4: ["SHAME_STIGMA", "COPING_STRATEGY"],
        5: ["SOCIAL_ISOLATION"],
        6: ["SOCIAL_ISOLATION"],
        7: ["FINANCIAL_STRESS"],
        8: ["FINANCIAL_STRESS"],
        9: ["FINANCIAL_STRESS", "COPING_STRATEGY"],
    }
    assignments_b = {
        0: ["FINANCIAL_STRESS"],
        1: ["HOUSING_PRECARITY"],
        2: ["FINANCIAL_STRESS", "COPING_STRATEGY"],  # DISAGREE on seg 2
        3: ["COPING_STRATEGY"],
        4: ["SHAME_STIGMA"],  # DISAGREE on seg 4 (missing COPING_STRATEGY)
        5: ["SOCIAL_ISOLATION"],
        6: ["SOCIAL_ISOLATION"],
        7: ["FINANCIAL_STRESS"],
        8: ["FINANCIAL_STRESS"],
        9: ["FINANCIAL_STRESS", "COPING_STRATEGY"],
    }

    run_id_a = make_run("coder_a")
    run_id_b = make_run("coder_b")

    seg_by_index = {s["segment_index"]: s for s in segments}

    for assignments, run_id, role in [
        (assignments_a, run_id_a, "coder_a"),
        (assignments_b, run_id_b, "coder_b"),
    ]:
        agent_id = agents[role]["id"]
        for seg_idx, code_names in assignments.items():
            seg = seg_by_index.get(seg_idx)
            if not seg:
                continue
            for j, code_name in enumerate(code_names):
                code_id = code_by_name.get(code_name)
                if code_id:
                    insert(conn, "assignment", {
                        "coding_run_id": run_id,
                        "segment_id": seg["id"],
                        "code_id": code_id,
                        "agent_id": agent_id,
                        "confidence": 0.85,
                        "rationale": f"Test rationale for {code_name}",
                        "is_primary": 1 if j == 0 else 0,
                    })

    conn.commit()
    return run_id_a, run_id_b


@pytest.fixture
def coding_run_ids_3way(conn, project_id, document_id, codebook_version_id):
    """
    Create three completed coding runs (A, B, supervisor) with simulated assignments.
    Returns (run_id_a, run_id_b, run_id_c).
    """
    from polyphony.db import fetchall, fetchone

    agents = {
        row["role"]: row
        for row in fetchall(conn, "SELECT * FROM agent WHERE project_id = ?", (project_id,))
    }
    segments = fetchall(conn, "SELECT * FROM segment WHERE project_id = ?", (project_id,))
    codes = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ?", (codebook_version_id,))
    code_by_name = {c["name"]: c["id"] for c in codes}

    def make_run(agent_role: str) -> int:
        run_id = insert(conn, "coding_run", {
            "project_id": project_id,
            "codebook_version_id": codebook_version_id,
            "agent_id": agents[agent_role]["id"],
            "run_type": "independent",
            "status": "complete",
            "segment_count": len(segments),
        })
        return run_id

    # A and B mostly agree, supervisor has a few differences
    assignments_a = {
        0: ["FINANCIAL_STRESS"],
        1: ["HOUSING_PRECARITY"],
        2: ["FINANCIAL_STRESS"],
        3: ["COPING_STRATEGY"],
        4: ["SHAME_STIGMA", "COPING_STRATEGY"],
        5: ["SOCIAL_ISOLATION"],
        6: ["SOCIAL_ISOLATION"],
        7: ["FINANCIAL_STRESS"],
        8: ["FINANCIAL_STRESS"],
        9: ["FINANCIAL_STRESS", "COPING_STRATEGY"],
    }
    assignments_b = {
        0: ["FINANCIAL_STRESS"],
        1: ["HOUSING_PRECARITY"],
        2: ["FINANCIAL_STRESS", "COPING_STRATEGY"],
        3: ["COPING_STRATEGY"],
        4: ["SHAME_STIGMA"],
        5: ["SOCIAL_ISOLATION"],
        6: ["SOCIAL_ISOLATION"],
        7: ["FINANCIAL_STRESS"],
        8: ["FINANCIAL_STRESS"],
        9: ["FINANCIAL_STRESS", "COPING_STRATEGY"],
    }
    assignments_c = {
        0: ["FINANCIAL_STRESS"],
        1: ["HOUSING_PRECARITY"],
        2: ["FINANCIAL_STRESS"],
        3: ["COPING_STRATEGY"],
        4: ["SHAME_STIGMA"],
        5: ["SOCIAL_ISOLATION"],
        6: ["SOCIAL_ISOLATION"],
        7: ["FINANCIAL_STRESS"],
        8: ["FINANCIAL_STRESS"],
        9: ["FINANCIAL_STRESS"],  # supervisor doesn't add COPING_STRATEGY
    }

    run_id_a = make_run("coder_a")
    run_id_b = make_run("coder_b")
    run_id_c = make_run("supervisor")

    seg_by_index = {s["segment_index"]: s for s in segments}

    for assignments, run_id, role in [
        (assignments_a, run_id_a, "coder_a"),
        (assignments_b, run_id_b, "coder_b"),
        (assignments_c, run_id_c, "supervisor"),
    ]:
        agent_id = agents[role]["id"]
        for seg_idx, code_names in assignments.items():
            seg = seg_by_index.get(seg_idx)
            if not seg:
                continue
            for j, code_name in enumerate(code_names):
                code_id = code_by_name.get(code_name)
                if code_id:
                    insert(conn, "assignment", {
                        "coding_run_id": run_id,
                        "segment_id": seg["id"],
                        "code_id": code_id,
                        "agent_id": agent_id,
                        "confidence": 0.85,
                        "rationale": f"Test rationale for {code_name}",
                        "is_primary": 1 if j == 0 else 0,
                    })

    conn.commit()
    return run_id_a, run_id_b, run_id_c
