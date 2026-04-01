"""Scenario-based integration tests for coding pipeline orchestration."""

from __future__ import annotations

import json

from polyphony.db import fetchall, fetchone, insert
from polyphony.pipeline.coding import code_segment, run_coding_session


class StubCodingAgent:
    """Deterministic agent for coding pipeline tests."""

    def __init__(self, agent_id: int, role: str, code_name: str | None = "FINANCIAL_STRESS"):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "stub"
        self.info = f"{role} (stub)"
        self._code_name = code_name

    def call(self, call_type, system_prompt, user_prompt, images=None):
        assignments = []
        if self._code_name is not None:
            assignments.append(
                {
                    "code_name": self._code_name,
                    "confidence": 0.9,
                    "rationale": "deterministic test assignment",
                    "is_primary": True,
                }
            )
        payload = {"assignments": assignments, "flags": []}
        return json.dumps(payload), payload, 1

    def update_call_link(self, call_id, **links):
        # No-op for test stub; we only care about orchestration semantics.
        return None


def _project(conn, project_id):
    p = fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
    assert p is not None
    return p


def _agent_id(conn, project_id, role):
    row = fetchone(
        conn,
        "SELECT id FROM agent WHERE project_id = ? AND role = ?",
        (project_id, role),
    )
    assert row is not None
    return row["id"]


def test_run_coding_session_independent_codes_all_segments(conn, project_id, document_id, codebook_version_id):
    project = _project(conn, project_id)
    agent = StubCodingAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")

    run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="independent",
    )

    total_segments = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?",
        (project_id,),
    )
    assert total_segments is not None

    coded_segments = fetchone(
        conn,
        "SELECT COUNT(DISTINCT segment_id) AS n FROM assignment WHERE coding_run_id = ?",
        (run_id,),
    )
    assert coded_segments is not None

    run_row = fetchone(conn, "SELECT * FROM coding_run WHERE id = ?", (run_id,))
    assert run_row is not None
    assert run_row["status"] == "complete"
    assert coded_segments["n"] == total_segments["n"]


def test_run_coding_session_calibration_only(conn, project_id, document_id, codebook_version_id):
    project = _project(conn, project_id)
    agent = StubCodingAgent(_agent_id(conn, project_id, "coder_b"), "coder_b")

    run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="calibration",
    )

    calibration_segments = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ? AND is_calibration = 1",
        (project_id,),
    )
    assert calibration_segments is not None

    coded_segments = fetchone(
        conn,
        "SELECT COUNT(DISTINCT segment_id) AS n FROM assignment WHERE coding_run_id = ?",
        (run_id,),
    )
    assert coded_segments is not None
    assert coded_segments["n"] == calibration_segments["n"]


def test_run_coding_session_resume_skips_already_coded(conn, project_id, document_id, codebook_version_id):
    project = _project(conn, project_id)
    agent_id = _agent_id(conn, project_id, "coder_a")
    agent = StubCodingAgent(agent_id, "coder_a")

    existing_run_id = insert(
        conn,
        "coding_run",
        {
            "project_id": project_id,
            "codebook_version_id": codebook_version_id,
            "agent_id": agent_id,
            "run_type": "independent",
            "status": "running",
        },
    )

    first_segment = fetchone(
        conn,
        "SELECT id FROM segment WHERE project_id = ? ORDER BY id LIMIT 1",
        (project_id,),
    )
    first_code = fetchone(
        conn,
        "SELECT id FROM code WHERE codebook_version_id = ? ORDER BY id LIMIT 1",
        (codebook_version_id,),
    )
    assert first_segment is not None
    assert first_code is not None

    insert(
        conn,
        "assignment",
        {
            "coding_run_id": existing_run_id,
            "segment_id": first_segment["id"],
            "code_id": first_code["id"],
            "agent_id": agent_id,
            "confidence": 0.95,
            "rationale": "pre-existing assignment",
            "is_primary": 1,
        },
    )
    conn.commit()

    resumed_run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="independent",
        resume=True,
    )

    assert resumed_run_id == existing_run_id

    total_segments = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?",
        (project_id,),
    )
    coded_segments = fetchone(
        conn,
        "SELECT COUNT(DISTINCT segment_id) AS n FROM assignment WHERE coding_run_id = ?",
        (resumed_run_id,),
    )
    run_row = fetchone(conn, "SELECT * FROM coding_run WHERE id = ?", (resumed_run_id,))

    assert total_segments is not None
    assert coded_segments is not None
    assert run_row is not None
    assert run_row["status"] == "complete"
    assert coded_segments["n"] == total_segments["n"]


def test_run_coding_session_non_resume_supersedes_existing_running(conn, project_id, document_id, codebook_version_id):
    project = _project(conn, project_id)
    agent_id = _agent_id(conn, project_id, "coder_b")
    agent = StubCodingAgent(agent_id, "coder_b")

    old_run_id = insert(
        conn,
        "coding_run",
        {
            "project_id": project_id,
            "codebook_version_id": codebook_version_id,
            "agent_id": agent_id,
            "run_type": "independent",
            "status": "running",
        },
    )
    conn.commit()

    new_run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="independent",
        resume=False,
    )

    assert new_run_id != old_run_id

    old_row = fetchone(conn, "SELECT * FROM coding_run WHERE id = ?", (old_run_id,))
    new_row = fetchone(conn, "SELECT * FROM coding_run WHERE id = ?", (new_run_id,))

    assert old_row is not None
    assert new_row is not None
    assert old_row["status"] == "error"
    assert "Superseded by new run" in (old_row["error_message"] or "")
    assert new_row["status"] == "complete"


def test_code_segment_unknown_code_raises_missing_code_flag(conn, project_id, document_id, codebook_version_id):
    project = _project(conn, project_id)
    agent_id = _agent_id(conn, project_id, "coder_a")
    agent = StubCodingAgent(agent_id, "coder_a", code_name="NOT_IN_CODEBOOK")

    run_id = insert(
        conn,
        "coding_run",
        {
            "project_id": project_id,
            "codebook_version_id": codebook_version_id,
            "agent_id": agent_id,
            "run_type": "independent",
            "status": "running",
        },
    )

    segment = fetchone(
        conn,
        "SELECT * FROM segment WHERE project_id = ? ORDER BY id LIMIT 1",
        (project_id,),
    )
    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? AND is_active = 1 ORDER BY sort_order",
        (codebook_version_id,),
    )
    assert segment is not None

    saved = code_segment(
        agent=agent,
        segment=segment,
        codes=codes,
        project=project,
        coding_run_id=run_id,
        conn=conn,
        document_name="test_doc.txt",
        total_segments=1,
    )

    assert saved == []

    flags = fetchall(
        conn,
        "SELECT * FROM flag WHERE project_id = ? AND segment_id = ? ORDER BY id",
        (project_id, segment["id"]),
    )
    assert len(flags) == 1
    assert flags[0]["flag_type"] == "missing_code"
    assert "NOT_IN_CODEBOOK" in (flags[0]["description"] or "")
