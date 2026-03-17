"""Tests for the database layer."""

import pytest
from polyphony.db import connect, fetchall, fetchone, insert, update, from_json, json_col


def test_connect_creates_tables(db_path):
    """Database should be created with all expected tables."""
    conn = connect(db_path)
    tables = [
        row["name"] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    expected = [
        "project", "agent", "document", "segment",
        "codebook_version", "code", "coding_run", "assignment",
        "llm_call", "irr_run", "irr_disagreement",
        "flag", "discussion_turn", "memo", "schema_migration",
    ]
    for t in expected:
        assert t in tables, f"Missing table: {t}"
    conn.close()


def test_migrations_run_once(db_path):
    """Migrations should not re-apply on reconnect."""
    for _ in range(3):
        conn = connect(db_path)
        versions = fetchall(conn, "SELECT * FROM schema_migration")
        conn.close()
    # Should have exactly 1 migration applied (001_initial.sql)
    assert len(versions) == 1


def test_insert_and_fetchone(conn):
    """Basic insert + fetchone round-trip."""
    row_id = insert(conn, "project", {
        "name": "Round Trip Test",
        "slug": "round-trip",
        "methodology": "thematic_analysis",
        "status": "setup",
        "config": "{}",
    })
    conn.commit()
    row = fetchone(conn, "SELECT * FROM project WHERE id = ?", (row_id,))
    assert row["name"] == "Round Trip Test"
    assert row["slug"] == "round-trip"


def test_fetchall_returns_list(conn, project_id):
    """fetchall should return a list of dicts."""
    rows = fetchall(conn, "SELECT * FROM agent WHERE project_id = ?", (project_id,))
    assert isinstance(rows, list)
    assert all(isinstance(r, dict) for r in rows)
    assert len(rows) == 3  # supervisor + coder_a + coder_b


def test_update(conn, project_id):
    """update() should modify rows in place."""
    update(conn, "project", {"status": "done"}, "id = ?", (project_id,))
    conn.commit()
    row = fetchone(conn, "SELECT status FROM project WHERE id = ?", (project_id,))
    assert row["status"] == "done"


def test_json_col_roundtrip():
    """JSON serialisation helpers."""
    data = {"key": [1, 2, 3], "nested": {"a": "b"}}
    serialised = json_col(data)
    assert isinstance(serialised, str)
    recovered = from_json(serialised)
    assert recovered == data


def test_from_json_handles_none():
    assert from_json(None, default=[]) == []
    assert from_json(None) is None


def test_foreign_key_enforcement(conn, project_id):
    """Foreign keys must be enforced (bad agent_id should fail)."""
    import sqlite3 as _sqlite3
    with pytest.raises(_sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO assignment (coding_run_id, segment_id, code_id, agent_id, is_primary) "
            "VALUES (9999, 9999, 9999, 9999, 1)"
        )
