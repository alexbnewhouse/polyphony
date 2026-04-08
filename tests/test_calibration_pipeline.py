"""Scenario-based integration tests for calibration pipeline behavior."""

from __future__ import annotations

import json
import threading

from polyphony.db import fetchone
from polyphony.pipeline.calibration import mark_calibration_set, run_calibration


class StubCalibrationAgent:
    """Deterministic agent for calibration/coding sessions."""

    def __init__(self, agent_id: int, role: str, code_name: str = "FINANCIAL_STRESS"):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "stub"
        self.info = f"{role} (stub)"
        self._code_name = code_name
        self.conn = None  # set by caller or parallelization code

    def call(self, call_type, system_prompt, user_prompt, images=None):
        payload = {
            "assignments": [
                {
                    "code_name": self._code_name,
                    "confidence": 0.9,
                    "rationale": "deterministic calibration assignment",
                    "is_primary": True,
                }
            ],
            "flags": [],
        }
        return json.dumps(payload), payload, 1

    def update_call_link(self, call_id, **links):
        return None


class _NoopHumanSupervisor(StubCalibrationAgent):
    def __init__(self, agent_id: int, role: str = "supervisor", code_name: str = "FINANCIAL_STRESS"):
        super().__init__(agent_id=agent_id, role=role, code_name=code_name)
        self.model_name = "human"


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


def test_mark_calibration_set_keeps_existing_when_not_clearing(conn, project_id, document_id):
    before = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ? AND is_calibration = 1",
        (project_id,),
    )
    assert before is not None

    marked = mark_calibration_set(conn, project_id, n=7, clear_existing=False)

    after = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ? AND is_calibration = 1",
        (project_id,),
    )
    assert after is not None
    assert marked == before["n"]
    assert after["n"] == before["n"]


def test_mark_calibration_set_clear_existing_marks_requested_size(conn, project_id, document_id):
    marked = mark_calibration_set(conn, project_id, n=4, seed=11, clear_existing=True)

    count = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ? AND is_calibration = 1",
        (project_id,),
    )
    assert count is not None
    assert marked == 4
    assert count["n"] == 4


def test_run_calibration_two_way_threshold_met(conn, project_id, document_id, codebook_version_id):
    project = _project(conn, project_id)
    agent_a = StubCalibrationAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")
    agent_b = StubCalibrationAgent(_agent_id(conn, project_id, "coder_b"), "coder_b")

    results = run_calibration(
        conn=conn,
        project=project,
        agent_a=agent_a,
        agent_b=agent_b,
        codebook_version_id=codebook_version_id,
        irr_threshold=0.0,
        calibration_sample_size=3,
        max_rounds=1,
        include_supervisor=False,
        skip_memo_gate=True,
    )

    assert "krippendorff_alpha" in results
    assert results["scope"] == "calibration"

    status = fetchone(conn, "SELECT status FROM project WHERE id = ?", (project_id,))
    assert status is not None
    assert status["status"] == "coding"


def test_run_calibration_three_way_records_threeway_metrics(
    conn, project_id, document_id, codebook_version_id,
):
    project = _project(conn, project_id)
    agent_a = StubCalibrationAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")
    agent_b = StubCalibrationAgent(_agent_id(conn, project_id, "coder_b"), "coder_b")
    supervisor = _NoopHumanSupervisor(_agent_id(conn, project_id, "supervisor"))

    results = run_calibration(
        conn=conn,
        project=project,
        agent_a=agent_a,
        agent_b=agent_b,
        codebook_version_id=codebook_version_id,
        irr_threshold=0.0,
        calibration_sample_size=3,
        max_rounds=1,
        include_supervisor=True,
        supervisor_agent=supervisor,
        skip_memo_gate=True,
    )

    assert "krippendorff_alpha_3way" in results
    assert results["krippendorff_alpha_3way"] is not None

    last_irr = fetchone(
        conn,
        "SELECT * FROM irr_run WHERE project_id = ? ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    assert last_irr is not None
    assert last_irr["coding_run_c_id"] is not None


def test_run_calibration_below_threshold_allows_manual_stop(
    conn,
    project_id,
    document_id,
    codebook_version_id,
    monkeypatch,
):
    project = _project(conn, project_id)
    agent_a = StubCalibrationAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")
    agent_b = StubCalibrationAgent(_agent_id(conn, project_id, "coder_b"), "coder_b")

    call_counter = {"run": 100}

    def fake_run_coding_session(*args, **kwargs):
        call_counter["run"] += 1
        return call_counter["run"]

    def fake_compute_irr(*args, **kwargs):
        return {
            "irr_run_id": 999,
            "krippendorff_alpha": 0.1,
            "cohen_kappa": 0.1,
            "percent_agreement": 0.2,
            "segment_count": 3,
            "disagreement_count": 2,
            "scope": "calibration",
            "disagreements": [],
        }

    monkeypatch.setattr("polyphony.pipeline.calibration.run_coding_session", fake_run_coding_session)
    monkeypatch.setattr("polyphony.pipeline.calibration.compute_irr", fake_compute_irr)
    monkeypatch.setattr("polyphony.pipeline.calibration.Confirm.ask", lambda *a, **k: False)
    monkeypatch.setattr("polyphony.pipeline.calibration.Prompt.ask", lambda *a, **k: "Test rationale")

    results = run_calibration(
        conn=conn,
        project=project,
        agent_a=agent_a,
        agent_b=agent_b,
        codebook_version_id=codebook_version_id,
        irr_threshold=0.8,
        calibration_sample_size=3,
        max_rounds=1,
        include_supervisor=False,
        skip_memo_gate=True,
    )

    assert results["krippendorff_alpha"] == 0.1

    status = fetchone(conn, "SELECT status FROM project WHERE id = ?", (project_id,))
    assert status is not None
    assert status["status"] == "coding"


def test_run_calibration_parallel_uses_separate_threads(
    conn, project_id, document_id, codebook_version_id, monkeypatch,
):
    """Verify that calibration agents run in separate threads."""
    project = _project(conn, project_id)
    agent_a = StubCalibrationAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")
    agent_b = StubCalibrationAgent(_agent_id(conn, project_id, "coder_b"), "coder_b")

    thread_names = []
    original_run = None

    def tracking_run_coding(*args, **kwargs):
        thread_names.append(threading.current_thread().name)
        return original_run(*args, **kwargs)

    # Import the real function so we can wrap it
    from polyphony.pipeline.coding import run_coding_session as real_run
    original_run = real_run
    monkeypatch.setattr("polyphony.pipeline.calibration.run_coding_session", tracking_run_coding)

    results = run_calibration(
        conn=conn,
        project=project,
        agent_a=agent_a,
        agent_b=agent_b,
        codebook_version_id=codebook_version_id,
        irr_threshold=0.0,
        calibration_sample_size=3,
        max_rounds=1,
        include_supervisor=False,
        skip_memo_gate=True,
    )

    # Both agents should have run (2 coding sessions)
    assert len(thread_names) == 2
    # They should run in ThreadPoolExecutor threads, not the MainThread
    assert all("ThreadPoolExecutor" in name for name in thread_names)
