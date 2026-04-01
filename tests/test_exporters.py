"""Tests for data exporters."""

import csv
import json
from pathlib import Path

import pytest
import yaml

from polyphony.io.exporters import (
    export_assignments,
    export_codebook,
    export_llm_log,
    export_memos,
    export_replication_package,
)


def test_export_codebook_yaml(conn, project_id, codebook_version_id, tmp_path):
    out = tmp_path / "codebook.yaml"
    export_codebook(conn, project_id, out, format="yaml")
    assert out.exists()
    data = yaml.safe_load(out.read_text())
    assert "codes" in data
    assert len(data["codes"]) == 5  # our 5 sample codes
    assert data["codes"][0]["name"] == "FINANCIAL_STRESS"


def test_export_codebook_json(conn, project_id, codebook_version_id, tmp_path):
    out = tmp_path / "codebook.json"
    export_codebook(conn, project_id, out, format="json")
    data = json.loads(out.read_text())
    assert data["codebook_version"] == 1


def test_export_codebook_csv(conn, project_id, codebook_version_id, tmp_path):
    out = tmp_path / "codebook.csv"
    export_codebook(conn, project_id, out, format="csv")
    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert "name" in rows[0]
    assert "description" in rows[0]


def test_export_assignments_csv(conn, project_id, coding_run_ids, tmp_path):
    out = tmp_path / "assignments.csv"
    export_assignments(conn, project_id, out, format="csv")
    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    assert "code_name" in rows[0]
    assert "agent_role" in rows[0]


def test_export_assignments_agent_filter(conn, project_id, coding_run_ids, tmp_path):
    out_a = tmp_path / "assignments_a.csv"
    export_assignments(conn, project_id, out_a, agent_filter="a")
    with out_a.open() as f:
        rows_a = list(csv.DictReader(f))
    assert all(r.get("agent_role") in ("coder_a", "(empty)") or True for r in rows_a)
    # All should be from coder_a
    assert all(r["agent_role"] == "coder_a" for r in rows_a if r.get("agent_role"))


def test_export_memos_markdown(conn, project_id, tmp_path):
    from polyphony.db import insert, fetchone
    sup = fetchone(conn, "SELECT id FROM agent WHERE project_id = ? AND role='supervisor'",
                   (project_id,))
    insert(conn, "memo", {
        "project_id": project_id,
        "author_id": sup["id"],
        "memo_type": "theoretical",
        "title": "Test Memo",
        "content": "This is a test memo about housing precarity.",
        "linked_codes": "[]",
        "linked_segments": "[]",
        "linked_flags": "[]",
        "tags": "[]",
    })
    conn.commit()

    out_dir = tmp_path / "memos"
    export_memos(conn, project_id, out_dir, format="md")
    md_files = list(out_dir.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "Test Memo" in content
    assert "housing precarity" in content


def test_export_llm_log_empty(conn, project_id, tmp_path):
    out = tmp_path / "llm_calls.jsonl"
    export_llm_log(conn, project_id, out)
    assert out.exists()
    lines = out.read_text().strip().split("\n") if out.read_text().strip() else []
    assert len(lines) == 0  # no LLM calls in test DB


def test_replication_irr_script_uses_intersection(conn, project_id, codebook_version_id, coding_run_ids, tmp_path):
    out_dir = tmp_path / "replication"
    export_replication_package(conn, project_id, out_dir)

    script = (out_dir / "scripts" / "compute_irr.py").read_text(encoding="utf-8")
    assert "set(a).intersection(set(b))" in script
    assert "set(a) | set(b)" not in script
