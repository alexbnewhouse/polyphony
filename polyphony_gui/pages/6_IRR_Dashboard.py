"""
Polyphony GUI — IRR Dashboard
==============================
Measure and visualize inter-rater reliability between coders.
"""

import logging
from pathlib import Path

import streamlit as st
import pandas as pd

from polyphony_gui.components import (
    render_sidebar,
    require_project,
    build_coder_run_selector,
    style_irr_cell,
    format_irr_label,
    display_disagreement,
)
from polyphony_gui.services import safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_irr_results,
    get_coding_runs,
    update_project_status,
)

st.set_page_config(page_title="IRR Dashboard — Polyphony", page_icon="📊", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()

st.title("📊 IRR Dashboard")
st.markdown(f"**Project:** {p['name']}")
st.markdown(
    "Inter-rater reliability (IRR) measures how consistently the two AI coders apply codes. "
    "High agreement means your codebook is clear and the results are trustworthy."
)

# ── Compute IRR ───────────────────────────────────────────────────────────────
st.markdown("### Compute IRR")

runs = get_coding_runs(db_path, project_id)
ind_runs = [r for r in runs if r.get("run_type") == "independent" and r.get("status") == "complete"]
a_runs = [r for r in ind_runs if r.get("agent_role") == "coder_a"]
b_runs = [r for r in ind_runs if r.get("agent_role") == "coder_b"]

if not a_runs or not b_runs:
    st.info(
        "You need at least one completed independent coding run for each coder to compute IRR. "
        "Go to **Code Data** to run coding first."
    )
else:
    with st.form("irr_form"):
        col1, col2 = st.columns(2)
        with col1:
            run_a_options = {r["id"]: f"Coder A — Run {r['id']} ({(r.get('started_at') or '')[:10]})" for r in a_runs}
            run_a_id = st.selectbox("Coder A run", options=list(run_a_options.keys()),
                                    format_func=lambda x: run_a_options[x])
        with col2:
            run_b_options = {r["id"]: f"Coder B — Run {r['id']} ({(r.get('started_at') or '')[:10]})" for r in b_runs}
            run_b_id = st.selectbox("Coder B run", options=list(run_b_options.keys()),
                                    format_func=lambda x: run_b_options[x])

        scope = st.radio(
            "Scope",
            options=["all", "calibration"],
            format_func=lambda x: {
                "all": "All segments",
                "calibration": "Calibration set only",
            }[x],
            horizontal=True,
        )
        compute_btn = st.form_submit_button("Compute IRR", type="primary")

    if compute_btn:
        from polyphony.db.connection import connect
        from polyphony.pipeline.irr import compute_irr, find_disagreements, get_coding_matrix

        conn = connect(Path(db_path))
        with st.spinner("Computing agreement metrics…"):
            irr = compute_irr(conn, project_id, run_a_id, run_b_id, scope=scope)
            conn.commit()
            update_project_status(db_path, project_id, "irr")

        alpha = irr.get("krippendorff_alpha")
        kappa = irr.get("cohen_kappa")
        pct = irr.get("percent_agreement")

        # Store results in session state for display
        st.session_state["last_irr"] = {
            "alpha": alpha, "kappa": kappa, "pct": pct,
            "run_a_id": run_a_id, "run_b_id": run_b_id,
            "disagreements": irr.get("disagreements", []),
            "segment_count": irr.get("segment_count"),
            "scope": scope,
        }
        conn.close()
        st.rerun()

# ── Display results ───────────────────────────────────────────────────────────
if "last_irr" in st.session_state:
    irr_data = st.session_state["last_irr"]
    alpha = irr_data["alpha"]
    kappa = irr_data["kappa"]
    pct = irr_data["pct"]

    st.divider()
    st.markdown("### Results")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Krippendorff's α", f"{alpha:.3f}" if alpha is not None else "—",
                help=format_irr_label(alpha) if alpha is not None else None)
    col2.metric("Cohen's κ", f"{kappa:.3f}" if kappa is not None else "—")
    col3.metric("% Agreement", f"{pct:.1f}%" if pct is not None else "—")
    col4.metric("Segments compared", irr_data.get("segment_count") or "—")

    if alpha is not None and alpha >= 0.8:
        st.success(f"{format_irr_label(alpha)} — α = {alpha:.3f} ≥ 0.80")
    elif alpha is not None and alpha >= 0.67:
        st.warning(f"{format_irr_label(alpha)} — α = {alpha:.3f}. Consider refining ambiguous codes.")
    elif alpha is not None:
        st.error(f"{format_irr_label(alpha)} — α = {alpha:.3f}. Codebook likely needs revision.")

    # Gauge visualization
    if alpha is not None:
        import plotly.graph_objects as go

        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=round(alpha, 3),
            domain={"x": [0, 1], "y": [0, 1]},
            title={"text": "Krippendorff's α"},
            gauge={
                "axis": {"range": [0, 1]},
                "steps": [
                    {"range": [0, 0.67], "color": "#f8d7da"},
                    {"range": [0.67, 0.8], "color": "#fff3cd"},
                    {"range": [0.8, 1.0], "color": "#d4edda"},
                ],
                "threshold": {
                    "line": {"color": "#4F46E5", "width": 4},
                    "thickness": 0.75,
                    "value": 0.8,
                },
                "bar": {"color": "#4F46E5"},
            },
        ))
        fig.update_layout(height=260, margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

    # Per-code agreement (compute from assignments)
    st.markdown("### Per-Code Agreement")
    from polyphony.db.connection import connect

    conn = connect(Path(db_path))
    try:
        # For each code, compute percent agreement between the two runs
        from polyphony.pipeline.irr import get_coding_matrix

        codes_a, codes_b, all_codes = get_coding_matrix(
            conn,
            irr_data["run_a_id"],
            irr_data["run_b_id"],
            scope=irr_data.get("scope", "all"),
        )
        all_segs = set(codes_a.keys()) | set(codes_b.keys())

        per_code_rows = []
        for code in sorted(all_codes):
            agree = sum(
                1 for seg in all_segs
                if (code in codes_a.get(seg, set())) == (code in codes_b.get(seg, set()))
            )
            count_a = sum(1 for segs in codes_a.values() if code in segs)
            count_b = sum(1 for segs in codes_b.values() if code in segs)
            pct_code = (agree / len(all_segs) * 100) if all_segs else 0
            per_code_rows.append({
                "Code": code,
                "% Agreement": f"{pct_code:.1f}%",
                "Coder A uses": count_a,
                "Coder B uses": count_b,
            })

        if per_code_rows:
            df_per = pd.DataFrame(per_code_rows)

            def _style_pct(val):
                try:
                    v = float(str(val).replace("%", ""))
                    if v >= 80:
                        return "background-color: #d4edda"
                    elif v >= 60:
                        return "background-color: #fff3cd"
                    else:
                        return "background-color: #f8d7da"
                except (ValueError, TypeError):
                    return ""

            st.dataframe(
                df_per.style.map(_style_pct, subset=["% Agreement"]),
                use_container_width=True,
                hide_index=True,
            )
    except Exception as e:
        st.warning(safe_error_message(e, "Per-code agreement"))
    finally:
        conn.close()

    # Disagreements
    disagreements = irr_data.get("disagreements", [])
    if disagreements:
        st.markdown(f"### Disagreements ({len(disagreements)} segments)")
        st.caption(
            "These segments were coded differently by Coder A and Coder B. "
            "Review them in the **Discuss** tab to make final decisions."
        )
        shown = disagreements[:20]
        seg_ids = [d.get("segment_id") for d in shown if d.get("segment_id") is not None]
        seg_text_by_id = {}
        if seg_ids:
            placeholders = ",".join("?" for _ in seg_ids)
            conn2 = connect(Path(db_path))
            seg_rows = conn2.execute(
                f"SELECT id, text FROM segment WHERE id IN ({placeholders})",
                tuple(seg_ids),
            ).fetchall()
            conn2.close()
            seg_text_by_id = {row["id"]: row["text"] for row in seg_rows}

        for d in shown:
            seg_id = d.get("segment_id")
            seg_raw = seg_text_by_id.get(seg_id, "")
            seg_txt = (seg_raw[:250] + "…") if len(seg_raw) > 250 else seg_raw

            codes_a_str = ", ".join(sorted(d.get("codes_a", []))) or "(none)"
            codes_b_str = ", ".join(sorted(d.get("codes_b", []))) or "(none)"

            with st.expander(f"Segment {seg_id}: A={codes_a_str} | B={codes_b_str}"):
                st.markdown(f"> {seg_txt}")
                c1, c2 = st.columns(2)
                c1.markdown(f"**Coder A:** {codes_a_str}")
                c2.markdown(f"**Coder B:** {codes_b_str}")

# ── Historical IRR table ───────────────────────────────────────────────────────
all_irr = get_irr_results(db_path, project_id)
if all_irr:
    st.divider()
    st.markdown("### IRR History")
    rows = [{
        "ID": r["id"],
        "Scope": r.get("scope", "all"),
        "α": f"{r['krippendorff_alpha']:.3f}" if r.get("krippendorff_alpha") is not None else "—",
        "κ": f"{r['cohen_kappa']:.3f}" if r.get("cohen_kappa") is not None else "—",
        "% Agree": f"{r['percent_agreement']:.1f}%" if r.get("percent_agreement") is not None else "—",
        "Segments": r.get("segment_count") or "—",
        "Date": (r.get("computed_at") or "")[:10],
    } for r in all_irr]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

with st.expander("ℹ️ Understanding IRR metrics"):
    st.markdown("""
**Krippendorff's α** is the gold standard for inter-rater reliability in qualitative research.
It corrects for chance agreement and handles missing data. Values:

| α | Interpretation |
|---|----------------|
| ≥ 0.80 | Acceptable for published research |
| 0.67–0.79 | Tentative conclusions only |
| < 0.67 | Unreliable — revise codebook |

**Cohen's κ** is a pairwise agreement measure, also chance-corrected.

**% Agreement** is the simplest measure (fraction of segments with identical coding),
but does not account for chance, so it tends to look better than it is.

For multi-code assignments (a segment can have multiple codes), polyphony uses a
binary presence/absence matrix per code to compute all metrics.
""")
