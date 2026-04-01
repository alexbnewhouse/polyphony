"""Tests for the database layer."""

import pytest
from polyphony.db import (
    connect,
    fetchall,
    fetchone,
    find_project_db,
    from_json,
    insert,
    json_col,
    update,
    write_project_marker,
)


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
    # Should have exactly 4 migrations applied (001–004)
    assert len(versions) == 4


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


def test_write_project_marker_rejects_outside_projects_root(tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("POLYPHONY_PROJECTS_DIR", str(projects_root))

    cwd = tmp_path / "workspace"
    cwd.mkdir()
    outside_project_dir = tmp_path / "outside-project"
    outside_project_dir.mkdir()

    with pytest.raises(ValueError, match="outside the configured"):
        write_project_marker(cwd, outside_project_dir)


def test_find_project_db_rejects_external_marker_target(tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("POLYPHONY_PROJECTS_DIR", str(projects_root))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_project_dir = tmp_path / "outside-project"
    outside_project_dir.mkdir()

    (workspace / ".polyphony_project").write_text(str(outside_project_dir), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="outside the configured"):
        find_project_db(workspace)


def test_find_project_db_accepts_marker_within_projects_root(tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "demo-project"
    project_dir.mkdir(parents=True)
    db_path = project_dir / "project.db"
    db_path.touch()
    monkeypatch.setenv("POLYPHONY_PROJECTS_DIR", str(projects_root))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".polyphony_project").write_text(str(project_dir), encoding="utf-8")

    assert find_project_db(workspace) == db_path
