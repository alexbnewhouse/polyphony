"""Tests for inter-rater reliability calculations."""

import math

import pytest

from polyphony.pipeline.irr import (
    compute_cohen_kappa,
    compute_irr,
    compute_irr_multiway,
    compute_krippendorff_alpha,
    compute_percent_agreement,
    compute_percent_agreement_multiway,
    find_disagreements,
    find_disagreements_multiway,
    get_coding_matrix,
    get_coding_matrices,
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
# 3-way unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_krippendorff_alpha_3way_perfect():
    codes_a = {i: {"CODE_A"} for i in range(10)}
    codes_b = {i: {"CODE_A"} for i in range(10)}
    codes_c = {i: {"CODE_A"} for i in range(10)}
    alpha = compute_krippendorff_alpha(codes_a, codes_b, codes_c, all_codes=["CODE_A"])
    assert abs(alpha - 1.0) < 0.01


def test_krippendorff_alpha_3way_disagreement():
    codes_a = {i: {"CODE_A"} for i in range(10)}
    codes_b = {i: {"CODE_A"} for i in range(10)}
    codes_c = {i: {"CODE_B"} for i in range(10)}  # C disagrees completely
    alpha = compute_krippendorff_alpha(codes_a, codes_b, codes_c, all_codes=["CODE_A", "CODE_B"])
    # Should be less than perfect
    assert alpha < 0.5


def test_percent_agreement_multiway_perfect():
    codes_a = {1: {"A"}, 2: {"B"}}
    codes_b = {1: {"A"}, 2: {"B"}}
    codes_c = {1: {"A"}, 2: {"B"}}
    pct, agree, total = compute_percent_agreement_multiway(codes_a, codes_b, codes_c)
    assert pct == 1.0
    assert agree == 2


def test_percent_agreement_multiway_partial():
    codes_a = {1: {"A"}, 2: {"B"}, 3: {"C"}}
    codes_b = {1: {"A"}, 2: {"B"}, 3: {"C"}}
    codes_c = {1: {"A"}, 2: {"X"}, 3: {"C"}}  # seg 2 disagrees
    pct, agree, total = compute_percent_agreement_multiway(codes_a, codes_b, codes_c)
    assert total == 3
    assert agree == 2


def test_find_disagreements_multiway():
    codes_a = {1: {"A"}, 2: {"B"}, 3: {"C"}}
    codes_b = {1: {"A"}, 2: {"B"}, 3: {"C"}}
    codes_c = {1: {"A"}, 2: {"X"}, 3: {"C"}}
    result = find_disagreements_multiway([
        ("coder_a", codes_a), ("coder_b", codes_b), ("supervisor", codes_c),
    ])
    assert len(result) == 1
    assert result[0]["segment_id"] == 2
    assert result[0]["codes_by_role"]["supervisor"] == ["X"]


def test_find_disagreements_multiway_all_agree():
    codes_a = {1: {"A"}, 2: {"B"}}
    codes_b = {1: {"A"}, 2: {"B"}}
    codes_c = {1: {"A"}, 2: {"B"}}
    result = find_disagreements_multiway([
        ("a", codes_a), ("b", codes_b), ("c", codes_c),
    ])
    assert result == []


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


# ─────────────────────────────────────────────────────────────────────────────
# 3-way integration tests
# ─────────────────────────────────────────────────────────────────────────────


def test_get_coding_matrices_3way(conn, coding_run_ids_3way):
    run_id_a, run_id_b, run_id_c = coding_run_ids_3way
    codes_maps, all_codes = get_coding_matrices(conn, [run_id_a, run_id_b, run_id_c])
    assert len(codes_maps) == 3
    assert all(len(cm) > 0 for cm in codes_maps)
    assert len(all_codes) > 0


def test_compute_irr_multiway_integration(conn, project_id, coding_run_ids_3way):
    run_id_a, run_id_b, run_id_c = coding_run_ids_3way
    results = compute_irr_multiway(conn, project_id, [run_id_a, run_id_b, run_id_c])

    assert "krippendorff_alpha" in results
    assert "krippendorff_alpha_3way" in results
    assert "pairwise_kappas" in results
    assert results["segment_count"] > 0
    assert len(results["pairwise_kappas"]) == 3  # A-B, A-sup, B-sup
    assert "role_labels" in results
    assert len(results["role_labels"]) == 3


def test_compute_irr_multiway_saves_3way_columns(conn, project_id, coding_run_ids_3way):
    from polyphony.db import fetchone, fetchall

    run_id_a, run_id_b, run_id_c = coding_run_ids_3way
    results = compute_irr_multiway(conn, project_id, [run_id_a, run_id_b, run_id_c])

    saved = fetchone(conn, "SELECT * FROM irr_run WHERE id = ?", (results["irr_run_id"],))
    assert saved is not None
    assert saved["coding_run_c_id"] == run_id_c
    assert saved["krippendorff_alpha_3way"] is not None

    # Check that code_c is saved on disagreements
    disagreements = fetchall(
        conn,
        "SELECT * FROM irr_disagreement WHERE irr_run_id = ?",
        (results["irr_run_id"],),
    )
    # At least some disagreements should have code_c populated
    if disagreements:
        has_code_c = any(d.get("code_c") is not None for d in disagreements)
        assert has_code_c, "Expected code_c to be populated on at least one disagreement"
