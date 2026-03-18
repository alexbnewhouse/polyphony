import json
from pathlib import Path

import pytest
import yaml

from polyphony.io import importers, exporters
from polyphony.db.connection import connect, insert, fetchall


FIXTURES = Path(__file__).parent / "fixtures"


def test_read_json_and_csv_fixture_structure():
    """Verify read_json and read_csv return correct counts and tuple shape."""
    entries = importers.read_json(FIXTURES / "sample_documents_extended.json")
    assert isinstance(entries, list)
    assert len(entries) == 6
    # Each entry should be (text, metadata)
    assert isinstance(entries[0], tuple)
    assert isinstance(entries[0][0], str)
    assert isinstance(entries[0][1], dict)

    csv_entries = importers.read_csv(FIXTURES / "sample_documents.csv")
    assert isinstance(csv_entries, list)
    assert len(csv_entries) == 2
    assert isinstance(csv_entries[0], tuple)
    assert isinstance(csv_entries[0][0], str)
    assert isinstance(csv_entries[0][1], dict)


def test_import_documents_creates_rows(tmp_path):
    db_path = tmp_path / "project.db"
    conn = connect(db_path)

    project_id = insert(conn, "project", {"name": "Test", "slug": "test", "methodology": "grounded_theory"})

    summary = importers.import_documents(conn, project_id, [str(FIXTURES / "sample_documents.csv")])
    assert summary["documents_imported"] == 2
    assert summary["segments_created"] > 0

    docs = fetchall(conn, "SELECT * FROM document WHERE project_id = ?", (project_id,))
    assert len(docs) == 2


def test_export_codebook_yaml(conn, project_id, codebook_version_id, tmp_path):
    """Export codebook and verify YAML structure matches expected shape."""
    out = tmp_path / "codebook.yaml"
    exporters.export_codebook(conn, project_id, out, format="yaml")

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["codebook_version"] == 1
    assert isinstance(data["codes"], list)
    assert len(data["codes"]) == 5
    # Verify code structure
    code = data["codes"][0]
    assert "name" in code
    assert "description" in code
    assert "inclusion_criteria" in code
    assert "is_active" in code


def test_export_assignments_csv(conn, project_id, coding_run_ids, tmp_path):
    """Export assignments and verify CSV has expected rows."""
    out = tmp_path / "assignments.csv"
    exporters.export_assignments(conn, project_id, out, format="csv")

    import csv
    with out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    assert "code_name" in rows[0]
    assert "agent_role" in rows[0]

    # Verify agent filter works
    out_a = tmp_path / "assignments_a.csv"
    exporters.export_assignments(conn, project_id, out_a, format="csv", agent_filter="a")
    with out_a.open(encoding="utf-8") as f:
        rows_a = list(csv.DictReader(f))
    assert all(r["agent_role"] == "coder_a" for r in rows_a)


def test_export_llm_log_writes_jsonl(tmp_path):
    db_path = tmp_path / "project.db"
    conn = connect(db_path)

    project_id = insert(conn, "project", {"name": "LLMTest", "slug": "llmtest", "methodology": "grounded_theory"})
    agent_id = insert(conn, "agent", {"project_id": project_id, "role": "coder_a", "agent_type": "llm", "model_name": "test", "temperature": 0.1, "seed": 1})

    lines = (FIXTURES / "sample_llm_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        obj = json.loads(line)
        insert(conn, "llm_call", {
            "project_id": project_id,
            "agent_id": agent_id,
            "call_type": obj.get("call_type", "prompt"),
            "model_name": "test-model",
            "model_version": "v1",
            "temperature": 0.1,
            "seed": 1,
            "system_prompt": obj.get("prompt", ""),
            "user_prompt": obj.get("prompt", ""),
            "full_response": obj.get("response", ""),
            "called_at": obj.get("called_at", "2026-01-01T00:00:00Z"),
        })

    out = tmp_path / "llm_calls_out.jsonl"
    exporters.export_llm_log(conn, project_id, out)

    content = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == len(lines)
    # Verify each line is valid JSON with expected fields
    for row_str in content:
        row = json.loads(row_str)
        assert "agent_role" in row
        assert "full_response" in row
