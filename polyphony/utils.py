"""Shared utilities."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from .db import fetchone


def slugify(text: str) -> str:
    """Convert a project name to a URL/filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:50].strip("-")


def get_project_or_abort(conn: sqlite3.Connection, project_id: int) -> dict:
    """Fetch project by ID or raise ValueError."""
    project = fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
    if not project:
        raise ValueError(f"Project {project_id} not found.")
    return project


def get_agents(conn: sqlite3.Connection, project_id: int) -> dict:
    """
    Return a dict of {role: agent_dict} for the project.
    Keys: 'supervisor', 'coder_a', 'coder_b'.
    """
    from .db import fetchall
    rows = fetchall(conn, "SELECT * FROM agent WHERE project_id = ?", (project_id,))
    return {row["role"]: row for row in rows}


def build_agent_objects(conn: sqlite3.Connection, project_id: int, host: str = "http://localhost:11434"):
    """
    Instantiate agent objects from DB records.
    Returns (agent_a, agent_b, supervisor_agent).

    Supported ``agent_type`` values in the database:
    - ``"human"`` — interactive terminal coder (HumanAgent)
    - ``"llm"`` — local Ollama model (OllamaAgent)
    - ``"openai"`` — OpenAI-compatible API (OpenAIAgent)
    - ``"anthropic"`` — Anthropic Messages API (AnthropicAgent)
    """
    from .agents import OllamaAgent, HumanAgent, OpenAIAgent, AnthropicAgent

    agents_db = get_agents(conn, project_id)
    agent_a = agent_b = supervisor = None

    for role, agent_row in agents_db.items():
        agent_type = agent_row["agent_type"]

        if agent_type == "human":
            obj = HumanAgent(
                agent_id=agent_row["id"],
                project_id=project_id,
                conn=conn,
                role=role,
            )
        elif agent_type == "openai":
            obj = OpenAIAgent(
                agent_id=agent_row["id"],
                project_id=project_id,
                role=role,
                model_name=agent_row["model_name"],
                temperature=agent_row["temperature"] or 0.1,
                seed=agent_row["seed"] or 42,
                conn=conn,
            )
        elif agent_type == "anthropic":
            obj = AnthropicAgent(
                agent_id=agent_row["id"],
                project_id=project_id,
                role=role,
                model_name=agent_row["model_name"],
                temperature=agent_row["temperature"] or 0.1,
                seed=agent_row["seed"] or 42,
                conn=conn,
            )
        else:
            # Default: "llm" → Ollama
            obj = OllamaAgent(
                agent_id=agent_row["id"],
                project_id=project_id,
                role=role,
                model_name=agent_row["model_name"],
                temperature=agent_row["temperature"] or 0.1,
                seed=agent_row["seed"] or 42,
                conn=conn,
                host=host,
            )

        if role == "coder_a":
            agent_a = obj
        elif role == "coder_b":
            agent_b = obj
        elif role == "supervisor":
            supervisor = obj

    return agent_a, agent_b, supervisor


def get_active_codebook(conn: sqlite3.Connection, project_id: int) -> Optional[dict]:
    """Return the latest codebook version for a project, or None."""
    return fetchone(
        conn,
        "SELECT * FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (project_id,),
    )
