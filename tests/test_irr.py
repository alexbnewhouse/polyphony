"""Tests for inter-rater reliability calculations."""

import math

import pytest

from polyphony.pipeline.irr import (
    compute_cohen_kappa,
    compute_irr,
    compute_krippendorff_alpha,
    compute_percent_agreement,
    find_disagreements,
    get_coding_matrix,
)


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for metric functions
# ─────────────────────────────────────────────────────────────────────────────


def test_percent_agreement_perfect():
    codes_a = {1: {"CODE_A"}, 2: {"CODE_B"}, 3: {"CODE_A", "CODE_B"}}
    codes_b = {1: {"CODE_A"}, 2: {"CODE_B"}, 3: {"CODE_A", "CODE_B"}}
    pct, agree, total = compute_percent_agreement(codes_a, codes_b)
    assert pct == 1.0
    assert agree == 3


def test_percent_agreement_partial():
    codes_a = {1: {"CODE_A"}, 2: {"CODE_B"}, 3: {"CODE_A"}}
    codes_b = {1: {"CODE_A"}, 2: {"CODE_A"}, 3: {"CODE_A"}}  # seg 2 disagrees
    pct, agree, total = compute_percent_agreement(codes_a, codes_b)
    assert total == 3
    assert agree == 2
    assert abs(pct - 2 / 3) < 0.001


def test_percent_agreement_empty():
    pct, agree, total = compute_percent_agreement({}, {})
    assert pct == 0.0
    assert total == 0


def test_find_disagreements():
    codes_a = {1: {"CODE_A"}, 2: {"CODE_B"}, 3: {"CODE_A", "CODE_B"}}
    codes_b = {1: {"CODE_A"}, 2: {"CODE_A"}, 3: {"CODE_A"}}
    disagreements = find_disagreements(codes_a, codes_b)
    assert len(disagreements) == 2
    seg_ids = {d["segment_id"] for d in disagreements}
    assert 2 in seg_ids
    assert 3 in seg_ids

    # Verify the "only_in_a" / "only_in_b" fields
    for d in disagreements:
        if d["segment_id"] == 2:
            assert "CODE_B" in d["only_in_a"]
            assert "CODE_A" in d["only_in_b"]


def test_find_no_disagreements():
    codes_a = {1: {"CODE_A"}, 2: {"CODE_B"}}
    codes_b = {1: {"CODE_A"}, 2: {"CODE_B"}}
    assert find_disagreements(codes_a, codes_b) == []


def test_cohen_kappa_perfect():
    codes_a = {i: {"CODE_A"} for i in range(10)}
    codes_b = {i: {"CODE_A"} for i in range(10)}
    kappa = compute_cohen_kappa(codes_a, codes_b, ["CODE_A"])
    assert abs(kappa - 1.0) < 0.001


def test_krippendorff_alpha_perfect():
    codes_a = {i: {"CODE_A"} for i in range(10)}
    codes_b = {i: {"CODE_A"} for i in range(10)}
    alpha = compute_krippendorff_alpha(codes_a, codes_b, all_codes=["CODE_A"])
    assert abs(alpha - 1.0) < 0.01


def test_krippendorff_alpha_empty():
    alpha = compute_krippendorff_alpha({}, {}, all_codes=[])
    assert math.isnan(alpha)


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests using fixtures
# ─────────────────────────────────────────────────────────────────────────────


def test_get_coding_matrix(conn, coding_run_ids):
    run_id_a, run_id_b = coding_run_ids
    codes_a, codes_b, all_codes = get_coding_matrix(conn, run_id_a, run_id_b)
    assert len(codes_a) > 0
    assert len(codes_b) > 0
    assert len(all_codes) > 0


def test_compute_irr_integration(conn, project_id, coding_run_ids):
    run_id_a, run_id_b = coding_run_ids
    results = compute_irr(conn, project_id, run_id_a, run_id_b)

    assert "krippendorff_alpha" in results
    assert "cohen_kappa" in results
    assert "percent_agreement" in results
    assert results["segment_count"] > 0
    assert 0 <= results["percent_agreement"] <= 1.0

    # Our simulated data has mostly agreement — alpha should be > 0.5
    alpha = results["krippendorff_alpha"]
    if not math.isnan(alpha):
        assert alpha > 0.5, f"Expected reasonable alpha, got {alpha}"


def test_compute_irr_saves_to_db(conn, project_id, coding_run_ids):
    from polyphony.db import fetchone
    run_id_a, run_id_b = coding_run_ids
    results = compute_irr(conn, project_id, run_id_a, run_id_b)
    saved = fetchone(conn, "SELECT * FROM irr_run WHERE id = ?", (results["irr_run_id"],))
    assert saved is not None
    assert saved["project_id"] == project_id


def test_irr_disagreements_saved(conn, project_id, coding_run_ids):
    from polyphony.db import fetchall
    run_id_a, run_id_b = coding_run_ids
    results = compute_irr(conn, project_id, run_id_a, run_id_b)
    disagreements = fetchall(
        conn,
        "SELECT * FROM irr_disagreement WHERE irr_run_id = ?",
        (results["irr_run_id"],),
    )
    assert len(disagreements) == results["disagreement_count"]
    # Our simulated data has 2 disagreements (segments 2 and 4)
    assert results["disagreement_count"] == 2
