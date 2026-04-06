"""Tests for context-window-aware batching in the coding pipeline."""

from __future__ import annotations

import json

import pytest

from polyphony.db import fetchall, fetchone, insert
from polyphony.pipeline.coding import (
    code_batch,
    create_batches,
    estimate_tokens,
    get_model_context_window,
    run_coding_session,
    _build_segments_block,
)


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for token estimation and model context windows
# ─────────────────────────────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_short_string(self):
        assert estimate_tokens("hello") >= 1

    def test_empty_string(self):
        assert estimate_tokens("") == 1  # min 1

    def test_proportional(self):
        short = estimate_tokens("abc")
        long = estimate_tokens("abc" * 100)
        assert long > short

    def test_approximately_correct_ratio(self):
        # 350 chars at ~3.5 chars/token → ~100 tokens
        text = "x" * 350
        tokens = estimate_tokens(text)
        assert 90 <= tokens <= 110


class TestGetModelContextWindow:
    def test_exact_match_openai(self):
        assert get_model_context_window("gpt-4o") == 128_000

    def test_exact_match_anthropic(self):
        assert get_model_context_window("claude-sonnet-4-20250514") == 200_000

    def test_exact_match_ollama(self):
        assert get_model_context_window("llama3.1:8b") == 131_072

    def test_prefix_match(self):
        # "gpt-4o-2024-08-06" should match "gpt-4o" prefix
        assert get_model_context_window("gpt-4o-2024-08-06") == 128_000

    def test_unknown_model_falls_back(self):
        assert get_model_context_window("totally-unknown-model") == 8_192

    def test_empty_model_name(self):
        assert get_model_context_window("") == 8_192

    def test_none_model_name(self):
        assert get_model_context_window(None) == 8_192


# ─────────────────────────────────────────────────────────────────────────────
# Batching logic tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_segment(seg_id, text, document_id=1, segment_index=0, media_type=None):
    """Helper to create fake segment dicts."""
    d = {
        "id": seg_id,
        "document_id": document_id,
        "segment_index": segment_index,
        "text": text,
    }
    if media_type:
        d["media_type"] = media_type
        d["image_path"] = f"/tmp/img_{seg_id}.png"
    return d


def _make_codes():
    return [
        {"id": 1, "name": "CODE_A", "description": "Test code A",
         "level": "open", "is_active": 1, "sort_order": 0},
        {"id": 2, "name": "CODE_B", "description": "Test code B",
         "level": "open", "is_active": 1, "sort_order": 1},
    ]


def _make_project():
    return {
        "id": 1,
        "methodology": "grounded_theory",
        "research_questions": json.dumps(["What is the experience?"]),
    }


class TestCreateBatches:
    def test_small_segments_all_fit_one_batch(self):
        segments = [_make_segment(i, f"Short text {i}") for i in range(5)]
        batches = create_batches(
            segments=segments,
            codes=_make_codes(),
            project=_make_project(),
            model_name="gpt-4o",  # 128K context
            doc_map={1: "doc.txt"},
        )
        # 5 short segments should fit in a single batch with 128K context
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_tiny_context_forces_more_batches(self):
        # Use a model with small context window (8K)
        segments = [_make_segment(i, "x" * 2000, segment_index=i) for i in range(10)]
        batches = create_batches(
            segments=segments,
            codes=_make_codes(),
            project=_make_project(),
            model_name="gpt-4",  # 8K context
            doc_map={1: "doc.txt"},
        )
        # With 8K context and 2000-char segments, must split into multiple batches
        assert len(batches) > 1
        # All segments accounted for
        total = sum(len(b) for b in batches)
        assert total == 10

    def test_image_segments_go_solo(self):
        segments = [
            _make_segment(1, "Normal text"),
            _make_segment(2, "", media_type="image"),
            _make_segment(3, "More text"),
        ]
        batches = create_batches(
            segments=segments,
            codes=_make_codes(),
            project=_make_project(),
            model_name="gpt-4o",
            doc_map={1: "doc.txt"},
        )
        # Image segment must be isolated in its own batch
        image_batches = [b for b in batches if any(s.get("media_type") == "image" for s in b)]
        assert len(image_batches) == 1
        assert len(image_batches[0]) == 1

    def test_empty_segments_returns_empty(self):
        batches = create_batches(
            segments=[],
            codes=_make_codes(),
            project=_make_project(),
            model_name="gpt-4o",
            doc_map={},
        )
        assert batches == []

    def test_single_segment(self):
        batches = create_batches(
            segments=[_make_segment(1, "Only one segment")],
            codes=_make_codes(),
            project=_make_project(),
            model_name="gpt-4o",
            doc_map={1: "doc.txt"},
        )
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_all_segments_accounted_for(self):
        """No segments lost during batching."""
        segments = [_make_segment(i, f"Segment number {i} " * 50, segment_index=i)
                    for i in range(25)]
        batches = create_batches(
            segments=segments,
            codes=_make_codes(),
            project=_make_project(),
            model_name="llama3:8b",  # 8K context
            doc_map={1: "doc.txt"},
        )
        total = sum(len(b) for b in batches)
        assert total == 25


class TestBuildSegmentsBlock:
    def test_basic_output(self):
        segments = [
            _make_segment(42, "Hello world", document_id=1, segment_index=3),
        ]
        block = _build_segments_block(segments, {1: "interview.txt"})
        assert "seg_42" in block
        assert "interview.txt" in block
        assert "Hello world" in block

    def test_multiple_segments(self):
        segments = [
            _make_segment(1, "First", document_id=1, segment_index=0),
            _make_segment(2, "Second", document_id=1, segment_index=1),
        ]
        block = _build_segments_block(segments, {1: "doc.txt"})
        assert "seg_1" in block
        assert "seg_2" in block


# ─────────────────────────────────────────────────────────────────────────────
# Stub agent for batch coding integration tests
# ─────────────────────────────────────────────────────────────────────────────


class StubBatchAgent:
    """Agent that returns batch-format responses."""

    def __init__(self, agent_id: int, role: str, code_name: str = "FINANCIAL_STRESS"):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "stub"
        self.info = f"{role} (stub)"
        self._code_name = code_name
        self._last_user_prompt = None

    def call(self, call_type, system_prompt, user_prompt, images=None):
        self._last_user_prompt = user_prompt
        # Parse segment keys from the user prompt to build correct response
        import re
        seg_keys = re.findall(r"\[seg_(\d+)\]", user_prompt)

        segments_data = {}
        for seg_key in seg_keys:
            segments_data[f"seg_{seg_key}"] = {
                "assignments": [
                    {
                        "code_name": self._code_name,
                        "confidence": 0.85,
                        "rationale": "batch test assignment",
                        "is_primary": True,
                    }
                ],
                "flags": [],
            }

        payload = {"segments": segments_data}
        return json.dumps(payload), payload, 1

    def update_call_link(self, call_id, **links):
        return None


class StubSingleAgent:
    """Agent that returns single-segment format responses (for fallback)."""

    def __init__(self, agent_id: int, role: str, code_name: str = "FINANCIAL_STRESS"):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "stub"
        self.info = f"{role} (stub)"
        self._code_name = code_name

    def call(self, call_type, system_prompt, user_prompt, images=None):
        payload = {
            "assignments": [
                {
                    "code_name": self._code_name,
                    "confidence": 0.9,
                    "rationale": "deterministic test assignment",
                    "is_primary": True,
                }
            ],
            "flags": [],
        }
        return json.dumps(payload), payload, 1

    def update_call_link(self, call_id, **links):
        return None


def _project_row(conn, project_id):
    return fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))


def _agent_id(conn, project_id, role):
    row = fetchone(conn, "SELECT id FROM agent WHERE project_id = ? AND role = ?",
                   (project_id, role))
    return row["id"]


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests: batch mode coding session
# ─────────────────────────────────────────────────────────────────────────────


def test_run_coding_session_batch_codes_all_segments(
    conn, project_id, document_id, codebook_version_id
):
    """Batch=True coding session codes every segment and completes."""
    project = _project_row(conn, project_id)
    agent = StubBatchAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")

    run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="independent",
        batch=True,
    )

    total_segments = fetchone(
        conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?", (project_id,)
    )["n"]

    coded_segments = fetchone(
        conn,
        "SELECT COUNT(DISTINCT segment_id) AS n FROM assignment WHERE coding_run_id = ?",
        (run_id,),
    )["n"]

    run_row = fetchone(conn, "SELECT * FROM coding_run WHERE id = ?", (run_id,))
    assert run_row["status"] == "complete"
    assert coded_segments == total_segments


def test_run_coding_session_batch_false_is_default(
    conn, project_id, document_id, codebook_version_id
):
    """Default (batch=False) still works with single-segment agent."""
    project = _project_row(conn, project_id)
    agent = StubSingleAgent(_agent_id(conn, project_id, "coder_b"), "coder_b")

    run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="independent",
        batch=False,
    )

    run_row = fetchone(conn, "SELECT * FROM coding_run WHERE id = ?", (run_id,))
    assert run_row["status"] == "complete"


def test_run_coding_session_batch_calibration(
    conn, project_id, document_id, codebook_version_id
):
    """Batch mode works for calibration runs too."""
    project = _project_row(conn, project_id)
    agent = StubBatchAgent(_agent_id(conn, project_id, "coder_a"), "coder_a")

    run_id = run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type="calibration",
        batch=True,
    )

    cal_segments = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM segment WHERE project_id = ? AND is_calibration = 1",
        (project_id,),
    )["n"]

    coded_segments = fetchone(
        conn,
        "SELECT COUNT(DISTINCT segment_id) AS n FROM assignment WHERE coding_run_id = ?",
        (run_id,),
    )["n"]

    assert coded_segments == cal_segments


# ─────────────────────────────────────────────────────────────────────────────
# Prompt template loading tests
# ─────────────────────────────────────────────────────────────────────────────


def test_batch_prompt_templates_exist():
    """Batch prompt templates can be loaded from the library."""
    from polyphony.prompts import library as prompt_lib

    batch_open = prompt_lib["batch_coding"]
    assert batch_open is not None
    assert "batch_size" in batch_open.required_vars()
    assert "segments_block" in batch_open.required_vars()

    batch_deductive = prompt_lib["batch_deductive_coding"]
    assert batch_deductive is not None
    assert "batch_size" in batch_deductive.required_vars()


def test_batch_prompt_template_renders():
    """Batch template renders with all required variables."""
    from polyphony.prompts import library as prompt_lib

    tmpl = prompt_lib["batch_coding"]
    system, user = tmpl.render(
        methodology="grounded_theory",
        research_question="How do people cope?",
        codebook_version="1",
        codebook_formatted="CODE_A\n  Description: test",
        batch_size="3",
        segments_block="### [seg_1] Document: test.txt, Segment 0\n---\nHello\n---",
    )
    assert "codebook" in system.lower()
    assert "seg_1" in user
    assert "3" in user
