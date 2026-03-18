import json
from pathlib import Path

import pytest

from polyphony.io import importers, exporters
from polyphony.db.connection import connect, insert, fetchall


FIXTURES = Path(__file__).parent / "fixtures"


def test_read_json_and_csv_fixture_counts():
    jpath = FIXTURES / "sample_documents_extended.json"
    entries = importers.read_json(jpath)
    assert isinstance(entries, list)
    assert len(entries) == 6

    cpath = FIXTURES / "sample_documents.csv"
    csv_entries = importers.read_csv(cpath)
    assert isinstance(csv_entries, list)
    assert len(csv_entries) == 2


def test_import_documents_creates_rows(tmp_path):
    db_path = tmp_path / "project.db"
    conn = connect(db_path)

    # create a project
    project_id = insert(conn, "project", {"name": "Test", "slug": "test", "methodology": "grounded_theory"})

    # import CSV fixture
    summary = importers.import_documents(conn, project_id, [str(FIXTURES / "sample_documents.csv")])
    assert summary["documents_imported"] == 2
    assert summary["segments_created"] > 0

    docs = fetchall(conn, "SELECT * FROM document WHERE project_id = ?", (project_id,))
    assert len(docs) == 2


def test_export_llm_log_writes_jsonl(tmp_path):
    db_path = tmp_path / "project.db"
    conn = connect(db_path)

    # create a project and agents
    project_id = insert(conn, "project", {"name": "LLMTest", "slug": "llmtest", "methodology": "grounded_theory"})
    agent_a = insert(conn, "agent", {"project_id": project_id, "role": "coder_a", "agent_type": "llm", "model_name": "test", "temperature": 0.1, "seed": 1})

    # load sample llm calls and insert
    lines = (FIXTURES / "sample_llm_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    n = 0
    for line in lines:
        obj = json.loads(line)
        # normalize to expected llm_call columns used in exporter
        insert(conn, "llm_call", {
            "project_id": project_id,
            "agent_id": agent_a,
            "call_type": obj.get("call_type", "prompt"),
            "model_name": obj.get("model_name", "test-model"),
            "model_version": obj.get("model_version", "v1"),
            "temperature": obj.get("temperature", 0.1),
            "seed": obj.get("seed", 1),
            "system_prompt": obj.get("prompt", ""),
            "user_prompt": obj.get("prompt", ""),
            "full_response": obj.get("response", ""),
            "called_at": obj.get("called_at", "2026-01-01T00:00:00Z"),
        })
        n += 1

    out = tmp_path / "llm_calls_out.jsonl"
    exporters.export_llm_log(conn, project_id, out)

    content = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == n
