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
from itertools import combinations
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
    Thin wrapper around get_coding_matrices() for backward compatibility.
    """
    codes_maps, all_codes = get_coding_matrices(conn, [run_id_a, run_id_b], scope)
    return codes_maps[0], codes_maps[1], all_codes


def get_coding_matrices(
    conn: sqlite3.Connection,
    run_ids: List[int],
    scope: str = "all",
) -> Tuple[List[Dict[int, Set[str]]], List[str]]:
    """
    Generalized version of get_coding_matrix for N coders.
    Returns (list_of_codes_maps, all_code_names).
    Each codes_map: segment_id → set of code names.
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

    codes_maps = [load_run(rid) for rid in run_ids]

    # Apply scope filter
    if scope == "calibration":
        cal_segs = {
            row["id"]
            for row in fetchall(conn, "SELECT id FROM segment WHERE is_calibration = 1")
        }
        codes_maps = [{k: v for k, v in cm.items() if k in cal_segs} for cm in codes_maps]
    elif scope.startswith("code:"):
        target_code = scope[5:]
        codes_maps = [{k: v for k, v in cm.items() if target_code in v} for cm in codes_maps]

    all_codes: Set[str] = set()
    for cm in codes_maps:
        for s in cm.values():
            all_codes.update(s)

    return codes_maps, sorted(all_codes)


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
    Thin wrapper around compute_percent_agreement_multiway().
    """
    return compute_percent_agreement_multiway(codes_a, codes_b)


def compute_percent_agreement_multiway(
    *codes_maps: Dict[int, Set[str]],
) -> Tuple[float, int, int]:
    """
    Multi-rater percent agreement: proportion of segments where ALL coders agree.
    Returns (percent_agreement, n_agree, n_total).
    """
    if not codes_maps:
        return 0.0, 0, 0
    all_segs = set(codes_maps[0].keys()).intersection(*(cm.keys() for cm in codes_maps[1:]))
    if not all_segs:
        return 0.0, 0, 0
    agree = 0
    for seg_id in all_segs:
        seg_codes = [cm.get(seg_id, set()) for cm in codes_maps]
        if all(sc == seg_codes[0] for sc in seg_codes[1:]):
            agree += 1
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

    all_segs = sorted(set(codes_a.keys()).intersection(set(codes_b.keys())))
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
    *codes_maps: Dict[int, Set[str]],
    all_codes: List[str],
) -> float:
    """
    Krippendorff's alpha using nominal metric.
    Uses multi-label approach: binary matrix per code.
    Accepts N coders (variadic codes_maps).
    """
    import krippendorff

    if not codes_maps:
        return float("nan")
    all_segs = sorted(set(codes_maps[0].keys()).intersection(*(cm.keys() for cm in codes_maps[1:])))
    if len(all_segs) < 2 or not all_codes:
        return float("nan")

    # Build reliability data matrix: shape (n_raters, n_units=n_segs*n_codes)
    # Each unit is a (segment, code) pair; value is 0 or 1.
    reliability_rows = []
    for cm in codes_maps:
        units = []
        for code in all_codes:
            for seg in all_segs:
                units.append(1 if code in cm.get(seg, set()) else 0)
        reliability_rows.append(units)

    reliability_data = np.array(reliability_rows, dtype=float)
    # Perfect agreement: all raters gave the same values → alpha = 1.0
    if all(np.array_equal(reliability_data[0], reliability_data[i])
           for i in range(1, len(reliability_data))):
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


def find_disagreements_multiway(
    coded_roles: List[Tuple[str, Dict[int, Set[str]]]],
) -> List[dict]:
    """
    Return disagreements for N coders.
    coded_roles: list of (role_label, codes_map) tuples.
    Returns dicts with 'codes_by_role' mapping role → sorted code list.
    """
    all_segs = set()
    for _, cm in coded_roles:
        all_segs.update(cm.keys())

    disagreements = []
    for seg_id in sorted(all_segs):
        seg_codes = {role: cm.get(seg_id, set()) for role, cm in coded_roles}
        values = list(seg_codes.values())
        if any(v != values[0] for v in values[1:]):
            disagreements.append({
                "segment_id": seg_id,
                "codes_by_role": {role: sorted(codes) for role, codes in seg_codes.items()},
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
    alpha = compute_krippendorff_alpha(codes_a, codes_b, all_codes=all_codes)
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


def compute_irr_multiway(
    conn: sqlite3.Connection,
    project_id: int,
    run_ids: List[int],
    scope: str = "all",
    notes: Optional[str] = None,
) -> dict:
    """
    Compute IRR metrics for 3+ coders and save to DB.
    run_ids: [run_a, run_b, run_c, ...] — first two are the primary pair.
    Returns an irr_run dict with all computed values including pairwise kappas.
    """
    codes_maps, all_codes = get_coding_matrices(conn, run_ids, scope)

    # 3-way alpha
    alpha_3way = compute_krippendorff_alpha(*codes_maps, all_codes=all_codes)

    # Percent agreement (all coders)
    pct_agree, n_agree, n_total = compute_percent_agreement_multiway(*codes_maps)

    # Pairwise Cohen's kappa for all pairs
    role_labels = []
    for rid in run_ids:
        row = fetchone(
            conn,
            "SELECT a.role FROM coding_run r JOIN agent a ON a.id = r.agent_id WHERE r.id = ?",
            (rid,),
        )
        role_labels.append(row["role"] if row else f"run_{rid}")

    pairwise_kappas = {}
    for (i, label_i), (j, label_j) in combinations(enumerate(role_labels), 2):
        kappa = compute_cohen_kappa(codes_maps[i], codes_maps[j], all_codes)
        pairwise_kappas[f"{label_i}_vs_{label_j}"] = kappa

    # Also compute 2-way alpha for A vs B (first two runs) for backward compat
    alpha_ab = compute_krippendorff_alpha(codes_maps[0], codes_maps[1], all_codes=all_codes)
    kappa_ab = compute_cohen_kappa(codes_maps[0], codes_maps[1], all_codes)

    # Find multiway disagreements
    coded_roles = list(zip(role_labels, codes_maps))
    disagreements_multi = find_disagreements_multiway(coded_roles)
    # Also build 2-way disagreements for DB storage
    disagreements_ab = find_disagreements(codes_maps[0], codes_maps[1])

    # Save irr_run
    irr_data = {
        "project_id": project_id,
        "coding_run_a_id": run_ids[0],
        "coding_run_b_id": run_ids[1],
        "scope": scope,
        "krippendorff_alpha": None if alpha_ab != alpha_ab else alpha_ab,
        "cohen_kappa": None if kappa_ab != kappa_ab else kappa_ab,
        "percent_agreement": pct_agree,
        "segment_count": n_total,
        "disagreement_count": len(disagreements_multi),
        "notes": notes,
    }
    if len(run_ids) >= 3:
        irr_data["coding_run_c_id"] = run_ids[2]
        irr_data["krippendorff_alpha_3way"] = None if alpha_3way != alpha_3way else alpha_3way
        # Store pairwise kappas for A-vs-sup and B-vs-sup
        kappa_a_sup = pairwise_kappas.get(f"{role_labels[0]}_vs_{role_labels[2]}", float("nan"))
        kappa_b_sup = pairwise_kappas.get(f"{role_labels[1]}_vs_{role_labels[2]}", float("nan"))
        irr_data["cohen_kappa_a_sup"] = None if kappa_a_sup != kappa_a_sup else kappa_a_sup
        irr_data["cohen_kappa_b_sup"] = None if kappa_b_sup != kappa_b_sup else kappa_b_sup

    irr_run_id = insert(conn, "irr_run", irr_data)

    # Save disagreements
    codes_c = codes_maps[2] if len(codes_maps) >= 3 else None
    for d_ab in disagreements_ab:
        row_data = {
            "irr_run_id": irr_run_id,
            "segment_id": d_ab["segment_id"],
            "code_a": json.dumps(d_ab["codes_a"]),
            "code_b": json.dumps(d_ab["codes_b"]),
        }
        if codes_c is not None:
            row_data["code_c"] = json.dumps(sorted(codes_c.get(d_ab["segment_id"], set())))
        insert(conn, "irr_disagreement", row_data)

    # Also save any disagreements only visible in multiway (e.g. A==B but C differs)
    ab_seg_ids = {d["segment_id"] for d in disagreements_ab}
    for d_m in disagreements_multi:
        if d_m["segment_id"] not in ab_seg_ids:
            row_data = {
                "irr_run_id": irr_run_id,
                "segment_id": d_m["segment_id"],
                "code_a": json.dumps(d_m["codes_by_role"].get(role_labels[0], [])),
                "code_b": json.dumps(d_m["codes_by_role"].get(role_labels[1], [])),
            }
            if len(role_labels) >= 3:
                row_data["code_c"] = json.dumps(d_m["codes_by_role"].get(role_labels[2], []))
            insert(conn, "irr_disagreement", row_data)

    conn.commit()

    return {
        "irr_run_id": irr_run_id,
        "krippendorff_alpha": alpha_ab,
        "krippendorff_alpha_3way": alpha_3way,
        "cohen_kappa": kappa_ab,
        "pairwise_kappas": pairwise_kappas,
        "percent_agreement": pct_agree,
        "segment_count": n_total,
        "disagreement_count": len(disagreements_multi),
        "scope": scope,
        "disagreements": disagreements_multi,
        "role_labels": role_labels,
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
    # 3-way alpha if present
    alpha_3way = results.get("krippendorff_alpha_3way")
    if alpha_3way is not None:
        table.add_row(
            "Krippendorff's α (3-way)",
            f"{alpha_3way:.3f}" if alpha_3way == alpha_3way else "n/a",
            alpha_interp(alpha_3way),
        )

    table.add_row("Segments coded", str(results["segment_count"]), "")
    table.add_row("Disagreements", str(results["disagreement_count"]), "")

    console.print(table)

    # Pairwise kappa table when present
    pairwise = results.get("pairwise_kappas")
    if pairwise:
        pw_table = Table(title="Pairwise Cohen's κ", show_header=True)
        pw_table.add_column("Pair", style="bold")
        pw_table.add_column("κ", justify="right")
        pw_table.add_column("Interpretation")
        for pair_label, k in sorted(pairwise.items()):
            pw_table.add_row(
                pair_label.replace("_vs_", " vs "),
                f"{k:.3f}" if k == k else "n/a",
                kappa_interp(k),
            )
        console.print(pw_table)

    if alpha == alpha and alpha < 0.67:
        console.print(
            "\n[red]⚠ IRR below acceptable threshold (α < 0.67).[/]\n"
            "Recommended: run `polyphony calibrate run` to review disagreements "
            "and refine codebook definitions."
        )
