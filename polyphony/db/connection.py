"""
polyphony.db.connection
==================
SQLite connection management with automatic schema migration.

Every project lives in its own directory containing a single `project.db`
file. This module handles finding, creating, and upgrading that database.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

# Directory marker file written by `polyphony project open`
PROJECT_MARKER = ".polyphony_project"
DB_FILENAME = "project.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


# ─────────────────────────────────────────────────────────────────────────────
# Connection helpers
# ─────────────────────────────────────────────────────────────────────────────


def _row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Return rows as plain dicts rather than tuples."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (and migrate) a project database. Returns a connection."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = _row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _run_migrations(conn)
    return conn


@contextmanager
def get_conn(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a connection and commits on exit."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Project discovery
# ─────────────────────────────────────────────────────────────────────────────


def find_project_db(start: Path | None = None) -> Path:
    """
    Walk up from `start` (default: cwd) looking for a .polyphony_project marker
    file that points to the project directory. Returns the path to project.db.

    Raises FileNotFoundError if no project is found.
    """
    cwd = Path(start or Path.cwd())
    for directory in [cwd, *cwd.parents]:
        marker = directory / PROJECT_MARKER
        if marker.exists():
            project_dir = marker.read_text().strip()
            return Path(project_dir) / DB_FILENAME
    raise FileNotFoundError(
        "No polyphony project found. Run `polyphony project new` or `polyphony project open <slug>`."
    )


def project_db_path(projects_root: Path, slug: str) -> Path:
    """Return the canonical db path for a project slug."""
    return projects_root / slug / DB_FILENAME


def write_project_marker(cwd: Path, project_dir: Path) -> None:
    """Write (or overwrite) the .polyphony_project marker in cwd."""
    (cwd / PROJECT_MARKER).write_text(str(project_dir))


# ─────────────────────────────────────────────────────────────────────────────
# Migration runner
# ─────────────────────────────────────────────────────────────────────────────


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any pending SQL migrations from the migrations/ directory."""
    # Ensure the schema_migration table exists (bootstrapping)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version     INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()

    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migration").fetchall()
    }

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for mf in migration_files:
        # Extract version number from filename like "001_initial.sql"
        match = re.match(r"^(\d+)_", mf.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in applied:
            continue
        sql = mf.read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migration (version, name) VALUES (?, ?)",
            (version, mf.name),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience query helpers
# ─────────────────────────────────────────────────────────────────────────────


def fetchone(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return conn.execute(sql, params).fetchall()


_ALLOWED_TABLES = frozenset({
    "project", "agent", "document", "segment", "codebook_version", "code",
    "coding_run", "assignment", "llm_call", "irr_run", "irr_disagreement",
    "flag", "discussion_turn", "memo", "schema_migration",
})


def _validate_table(table: str) -> None:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Invalid table name: '{table}'")


def insert(conn: sqlite3.Connection, table: str, data: dict) -> int:
    """Insert a row and return the new rowid."""
    _validate_table(table)
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cursor = conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(data.values())
    )
    return cursor.lastrowid


def update(conn: sqlite3.Connection, table: str, data: dict, where: str, params: tuple) -> None:
    """Update rows in table."""
    _validate_table(table)
    setters = ", ".join(f"{k} = ?" for k in data)
    conn.execute(f"UPDATE {table} SET {setters} WHERE {where}", tuple(data.values()) + params)


def json_col(value) -> str:
    """Serialize a Python object to a JSON string for storage."""
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, default=None):
    """Deserialize a JSON column value."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
