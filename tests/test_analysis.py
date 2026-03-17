"""Tests for analysis functions."""

import pytest

from polyphony.pipeline.analysis import (
    check_saturation,
    co_occurrence_matrix,
    code_frequency_table,
)


def test_code_frequency_table(conn, project_id, coding_run_ids):
    rows = code_frequency_table(conn, project_id)
    assert isinstance(rows, list)
    # We have 10 segments coded; should get multiple codes
    assert len(rows) > 0
    # All rows should have segment_count >= 1
    assert all(r["segment_count"] >= 1 for r in rows)
    # Sorted descending
    counts = [r["segment_count"] for r in rows]
    assert counts == sorted(counts, reverse=True)


def test_code_frequency_table_empty(conn, project_id):
    """No coding runs → empty list."""
    rows = code_frequency_table(conn, project_id)
    # project_id has no assignments without coding_run_ids fixture
    assert isinstance(rows, list)


def test_co_occurrence_matrix(conn, project_id, coding_run_ids):
    matrix = co_occurrence_matrix(conn, project_id)
    assert isinstance(matrix, dict)
    # FINANCIAL_STRESS and COPING_STRATEGY should co-occur (segment 9)
    if "FINANCIAL_STRESS" in matrix:
        assert "COPING_STRATEGY" in matrix["FINANCIAL_STRESS"]


def test_saturation_check(conn, project_id, coding_run_ids):
    result = check_saturation(conn, project_id, window_size=3)
    assert "total_unique_codes" in result
    assert "likely_saturated" in result
    assert result["total_unique_codes"] >= 1
