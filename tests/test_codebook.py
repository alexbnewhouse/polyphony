"""Tests for codebook management."""

import json

import pytest

from polyphony.db import fetchall, fetchone
from polyphony.pipeline.induction import (
    merge_candidates,
    save_codebook_version,
    select_induction_sample,
)
from polyphony.prompts import format_codebook


# ─────────────────────────────────────────────────────────────────────────────
# Induction helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_merge_candidates_deduplication():
    candidates_a = [
        {"name": "FINANCIAL_STRESS", "description": "Financial worry", "level": "open"},
        {"name": "ISOLATION", "description": "Social isolation", "level": "open"},
    ]
    candidates_b = [
        {"name": "FINANCIAL_STRESS", "description": "Money anxiety", "level": "open"},
        {"name": "SHAME", "description": "Embarrassment", "level": "open"},
    ]
    merged = merge_candidates(candidates_a, candidates_b)
    names = [c["name"] for c in merged]
    # FINANCIAL_STRESS should appear only once
    assert names.count("FINANCIAL_STRESS") == 1
    assert len(merged) == 3  # FINANCIAL_STRESS, ISOLATION, SHAME


def test_merge_candidates_enriches_description():
    a = [{"name": "CODE_A", "description": "First description", "level": "open"}]
    b = [{"name": "CODE_A", "description": "Second description", "level": "open"}]
    merged = merge_candidates(a, b)
    assert len(merged) == 1
    # Both descriptions should be present
    assert "First description" in merged[0]["description"]
    assert "Second description" in merged[0]["description"]


def test_merge_candidates_empty():
    assert merge_candidates([], []) == []
    result = merge_candidates([{"name": "CODE_A", "description": "X", "level": "open"}], [])
    assert len(result) == 1


def test_select_induction_sample(conn, project_id, document_id):
    sample = select_induction_sample(conn, project_id, n=5, seed=42)
    assert len(sample) <= 5
    assert all("text" in s for s in sample)
    assert all("segment_index" in s for s in sample)


def test_select_induction_sample_stratified(conn, project_id, document_id):
    """Sample should include segments from the document."""
    sample = select_induction_sample(conn, project_id, n=10, seed=42)
    doc_ids = {s["document_id"] for s in sample}
    assert len(doc_ids) >= 1


def test_select_induction_sample_no_segments(conn, project_id):
    """Should raise ValueError when no segments exist."""
    with pytest.raises(ValueError, match="No segments"):
        select_induction_sample(conn, project_id, n=5)


def test_save_codebook_version(conn, project_id):
    codes = [
        {"name": "CODE_A", "description": "Test code A", "level": "open",
         "inclusion_criteria": "Include when...", "exclusion_criteria": "",
         "example_quotes": []},
    ]
    cb_id = save_codebook_version(conn, project_id, codes, version=1)
    assert cb_id > 0

    cb = fetchone(conn, "SELECT * FROM codebook_version WHERE id = ?", (cb_id,))
    assert cb["version"] == 1
    assert cb["stage"] == "draft"

    saved_codes = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ?", (cb_id,))
    assert len(saved_codes) == 1
    assert saved_codes[0]["name"] == "CODE_A"


# ─────────────────────────────────────────────────────────────────────────────
# Codebook formatting
# ─────────────────────────────────────────────────────────────────────────────


def test_format_codebook_empty():
    result = format_codebook([])
    assert "No codes" in result


def test_format_codebook_basic():
    codes = [
        {
            "name": "FINANCIAL_STRESS",
            "description": "Worry about finances",
            "level": "open",
            "inclusion_criteria": "When money is mentioned",
            "exclusion_criteria": "",
            "example_quotes": json.dumps(["I can't pay my bills"]),
            "is_active": 1,
            "sort_order": 0,
        }
    ]
    result = format_codebook(codes)
    assert "FINANCIAL_STRESS" in result
    assert "Worry about finances" in result
    assert "When money is mentioned" in result
    assert "OPEN CODES" in result


def test_format_codebook_groups_by_level():
    codes = [
        {"name": "CODE_OPEN", "description": "Open", "level": "open",
         "inclusion_criteria": None, "exclusion_criteria": None,
         "example_quotes": "[]", "is_active": 1, "sort_order": 0},
        {"name": "CODE_AXIAL", "description": "Axial", "level": "axial",
         "inclusion_criteria": None, "exclusion_criteria": None,
         "example_quotes": "[]", "is_active": 1, "sort_order": 0},
    ]
    result = format_codebook(codes)
    assert "OPEN CODES" in result
    assert "AXIAL CODES" in result
    assert result.index("OPEN CODES") < result.index("AXIAL CODES")
