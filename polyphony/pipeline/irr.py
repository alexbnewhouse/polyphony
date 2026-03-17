"""
polyphony.pipeline.irr
=================
Inter-rater reliability calculations.

Supported metrics:
  - Krippendorff's alpha (nominal scale, handles missing data)
  - Cohen's kappa (pairwise, exact agreement accounting for chance)
  - Percent agreement (simple baseline)

We treat each segment as a unit of analysis. For multi-code assignments
(a segment can receive multiple codes), we compare using a binary
present/absent matrix per code.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from rich.console import Console
from rich.table import Table

from ..db import fetchall, fetchone, insert

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Data preparation
# ─────────────────────────────────────────────────────────────────────────────


def get_coding_matrix(
    conn: sqlite3.Connection,
    run_id_a: int,
    run_id_b: int,
    scope: str = "all",
) -> Tuple[Dict[int, Set[str]], Dict[int, Set[str]], List[str]]:
    """
    Build coding matrices for two runs.
    Returns (codes_by_seg_a, codes_by_seg_b, all_code_names).
    Each dict maps segment_id → set of code names.
    """
    def load_run(run_id: int) -> Dict[int, Set[str]]:
        rows = fetchall(
            conn,
            """
            SELECT a.segment_id, c.name AS code_name
            FROM assignment a
            JOIN code c ON c.id = a.code_id
            WHERE a.coding_run_id = ?
            """,
            (run_id,),
        )
        result: Dict[int, Set[str]] = {}
        for row in rows:
            result.setdefault(row["segment_id"], set()).add(row["code_name"])
        return result

    codes_a = load_run(run_id_a)
    codes_b = load_run(run_id_b)

    # Apply scope filter
    if scope == "calibration":
        cal_segs = {
            row["id"]
            for row in fetchall(
                conn,
                "SELECT id FROM segment WHERE is_calibration = 1",
            )
        }
        codes_a = {k: v for k, v in codes_a.items() if k in cal_segs}
        codes_b = {k: v for k, v in codes_b.items() if k in cal_segs}
    elif scope.startswith("code:"):
        target_code = scope[5:]
        codes_a = {k: v for k, v in codes_a.items() if target_code in v}
        codes_b = {k: v for k, v in codes_b.items() if target_code in v}

    # All codes seen in either run
    all_codes: Set[str] = set()
    for s in codes_a.values():
        all_codes.update(s)
    for s in codes_b.values():
        all_codes.update(s)

    return codes_a, codes_b, sorted(all_codes)


# ─────────────────────────────────────────────────────────────────────────────
# Metric calculations
# ─────────────────────────────────────────────────────────────────────────────


def compute_percent_agreement(
    codes_a: Dict[int, Set[str]],
    codes_b: Dict[int, Set[str]],
) -> Tuple[float, int, int]:
    """
    Percent agreement: proportion of segments where both coders agree exactly.
    Returns (percent_agreement, n_agree, n_total).
    """
    all_segs = set(codes_a.keys()) | set(codes_b.keys())
    if not all_segs:
        return 0.0, 0, 0
    agree = sum(
        1
        for seg_id in all_segs
        if codes_a.get(seg_id, set()) == codes_b.get(seg_id, set())
    )
    return agree / len(all_segs), agree, len(all_segs)


def compute_cohen_kappa(
    codes_a: Dict[int, Set[str]],
    codes_b: Dict[int, Set[str]],
    all_codes: List[str],
) -> float:
    """
    Cohen's kappa averaged across all codes (one-vs-rest binary scheme).
    """
    from sklearn.metrics import cohen_kappa_score

    all_segs = sorted(set(codes_a.keys()) | set(codes_b.keys()))
    if len(all_segs) < 2:
        return float("nan")

    kappas = []
    for code in all_codes:
        y_a = [1 if code in codes_a.get(seg, set()) else 0 for seg in all_segs]
        y_b = [1 if code in codes_b.get(seg, set()) else 0 for seg in all_segs]
        if sum(y_a) == 0 and sum(y_b) == 0:
            # Both coders never applied this code → skip
            continue
        # Perfect agreement (all same value) → kappa = 1.0 by convention
        if y_a == y_b:
            kappas.append(1.0)
            continue
        try:
            k = cohen_kappa_score(y_a, y_b)
            if k == k:  # not NaN
                kappas.append(k)
        except Exception:
            pass

    return float(np.mean(kappas)) if kappas else float("nan")


def compute_krippendorff_alpha(
    codes_a: Dict[int, Set[str]],
    codes_b: Dict[int, Set[str]],
    all_codes: List[str],
) -> float:
    """
    Krippendorff's alpha using nominal metric.
    Uses multi-label approach: binary matrix per code.
    """
    import krippendorff

    all_segs = sorted(set(codes_a.keys()) | set(codes_b.keys()))
    if len(all_segs) < 2 or not all_codes:
        return float("nan")

    # Build reliability data matrix: shape (n_raters=2, n_units=n_segs*n_codes)
    # Each unit is a (segment, code) pair; value is 0 or 1.
    units_a = []
    units_b = []
    for code in all_codes:
        for seg in all_segs:
            units_a.append(1 if code in codes_a.get(seg, set()) else 0)
            units_b.append(1 if code in codes_b.get(seg, set()) else 0)

    reliability_data = np.array([units_a, units_b], dtype=float)
    # Perfect agreement: all raters gave the same values → alpha = 1.0
    if np.array_equal(reliability_data[0], reliability_data[1]):
        return 1.0
    try:
        alpha = krippendorff.alpha(reliability_data=reliability_data, level_of_measurement="nominal")
        return float(alpha)
    except Exception:
        return float("nan")


# ─────────────────────────────────────────────────────────────────────────────
# Disagreement extraction
# ─────────────────────────────────────────────────────────────────────────────


def find_disagreements(
    codes_a: Dict[int, Set[str]],
    codes_b: Dict[int, Set[str]],
) -> List[dict]:
    """
    Return a list of disagreement dicts for segments where coders differ.
    """
    all_segs = set(codes_a.keys()) | set(codes_b.keys())
    disagreements = []
    for seg_id in sorted(all_segs):
        a = codes_a.get(seg_id, set())
        b = codes_b.get(seg_id, set())
        if a != b:
            disagreements.append({
                "segment_id": seg_id,
                "codes_a": sorted(a),
                "codes_b": sorted(b),
                "only_in_a": sorted(a - b),
                "only_in_b": sorted(b - a),
            })
    return disagreements


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────


def compute_irr(
    conn: sqlite3.Connection,
    project_id: int,
    run_id_a: int,
    run_id_b: int,
    scope: str = "all",
    notes: Optional[str] = None,
) -> dict:
    """
    Compute all IRR metrics and save to DB.
    Returns an irr_run dict with all computed values.
    """
    codes_a, codes_b, all_codes = get_coding_matrix(conn, run_id_a, run_id_b, scope)

    pct_agree, n_agree, n_total = compute_percent_agreement(codes_a, codes_b)
    kappa = compute_cohen_kappa(codes_a, codes_b, all_codes)
    alpha = compute_krippendorff_alpha(codes_a, codes_b, all_codes)
    disagreements = find_disagreements(codes_a, codes_b)

    # Save irr_run
    run_id = insert(conn, "irr_run", {
        "project_id": project_id,
        "coding_run_a_id": run_id_a,
        "coding_run_b_id": run_id_b,
        "scope": scope,
        "krippendorff_alpha": None if alpha != alpha else alpha,  # NaN check
        "cohen_kappa": None if kappa != kappa else kappa,
        "percent_agreement": pct_agree,
        "segment_count": n_total,
        "disagreement_count": len(disagreements),
        "notes": notes,
    })

    # Save individual disagreements
    for d in disagreements:
        insert(conn, "irr_disagreement", {
            "irr_run_id": run_id,
            "segment_id": d["segment_id"],
            "code_a": json.dumps(d["codes_a"]),
            "code_b": json.dumps(d["codes_b"]),
        })

    conn.commit()

    return {
        "irr_run_id": run_id,
        "krippendorff_alpha": alpha,
        "cohen_kappa": kappa,
        "percent_agreement": pct_agree,
        "segment_count": n_total,
        "disagreement_count": len(disagreements),
        "scope": scope,
        "disagreements": disagreements,
    }


def print_irr_summary(results: dict) -> None:
    """Display IRR results as a Rich table."""
    table = Table(title="Inter-rater Reliability", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Interpretation")

    def alpha_interp(v):
        if v != v:
            return "[dim]n/a[/]"
        if v >= 0.80:
            return "[green]Excellent[/]"
        if v >= 0.67:
            return "[yellow]Acceptable[/]"
        return "[red]Poor — revise codebook[/]"

    def kappa_interp(v):
        if v != v:
            return "[dim]n/a[/]"
        if v >= 0.80:
            return "[green]Strong[/]"
        if v >= 0.60:
            return "[yellow]Moderate[/]"
        return "[red]Weak[/]"

    alpha = results["krippendorff_alpha"]
    kappa = results["cohen_kappa"]
    pct = results["percent_agreement"]

    table.add_row(
        "Krippendorff's α",
        f"{alpha:.3f}" if alpha == alpha else "n/a",
        alpha_interp(alpha),
    )
    table.add_row(
        "Cohen's κ",
        f"{kappa:.3f}" if kappa == kappa else "n/a",
        kappa_interp(kappa),
    )
    table.add_row(
        "% Agreement",
        f"{pct:.1%}",
        "",
    )
    table.add_row("Segments coded", str(results["segment_count"]), "")
    table.add_row("Disagreements", str(results["disagreement_count"]), "")

    console.print(table)

    if alpha == alpha and alpha < 0.67:
        console.print(
            "\n[red]⚠ IRR below acceptable threshold (α < 0.67).[/]\n"
            "Recommended: run `polyphony calibrate run` to review disagreements "
            "and refine codebook definitions."
        )
