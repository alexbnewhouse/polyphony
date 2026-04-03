"""
Polyphony GUI — Calibrate
==========================
Run calibration rounds so both AI coders align on the codebook before
full independent coding.
"""

import logging
from pathlib import Path

import streamlit as st

from polyphony_gui.components import render_sidebar, require_project
from polyphony_gui.components import style_irr_cell, format_irr_label
from polyphony_gui.services import safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_codebook,
    get_codes,
    get_irr_results,
    update_project_status,
)

st.set_page_config(page_title="Calibrate — Polyphony", page_icon="⚖️", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()

st.title("⚖️ Calibrate")
st.markdown(f"**Project:** {p['name']}")

cb = get_codebook(db_path, project_id)
if not cb:
    st.warning("No codebook found. Please create a codebook first (go to **Codebook**).")
    st.stop()

codes = get_codes(db_path, cb["id"])
if not codes:
    st.warning("The codebook has no active codes. Please add codes first.")
    st.stop()

st.markdown(
    "Calibration runs both AI coders on the same small set of segments, then measures "
    "how much they agree. This helps catch codebook ambiguities before full coding."
)

# ── Previous calibration results ─────────────────────────────────────────────
irr_runs = get_irr_results(db_path, project_id)
cal_runs = [r for r in irr_runs if r.get("run_type") == "calibration"]

if cal_runs:
    st.markdown("### Previous Calibration Results")
    import pandas as pd

    rows = []
    for r in cal_runs[:5]:
        alpha = r.get("krippendorff_alpha")
        kappa = r.get("cohen_kappa")
        pct = r.get("percent_agreement")
        rows.append({
            "Run": r["id"],
            "Date": (r.get("created_at") or "")[:16],
            "Krippendorff's α": f"{alpha:.3f}" if alpha is not None else "—",
            "Cohen's κ": f"{kappa:.3f}" if kappa is not None else "—",
            "% Agreement": f"{pct:.1f}%" if pct is not None else "—",
            "Segments": r.get("segment_count") or "—",
        })
    df = pd.DataFrame(rows)

    def _color_alpha(val):
        try:
            v = float(str(val))
            if v >= 0.8:
                return "background-color: #d4edda"
            elif v >= 0.6:
                return "background-color: #fff3cd"
            else:
                return "background-color: #f8d7da"
        except (ValueError, TypeError):
            return ""

    st.dataframe(
        df.style.map(_color_alpha, subset=["Krippendorff's α"]),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Green ≥ 0.80 (acceptable), Yellow ≥ 0.60 (moderate), Red < 0.60 (poor)")

# ── Run calibration ───────────────────────────────────────────────────────────
st.divider()
st.markdown("### Run New Calibration")

with st.form("calibration_form"):
    n_cal = st.slider(
        "Number of calibration segments",
        min_value=5,
        max_value=40,
        value=10,
        help="How many segments should both coders analyze in the calibration round?",
    )
    threshold = st.slider(
        "Acceptable agreement threshold (Krippendorff's α)",
        min_value=0.5,
        max_value=1.0,
        value=0.8,
        step=0.05,
        help="α ≥ 0.80 is generally considered acceptable in qualitative research.",
    )
    clear_existing = st.checkbox(
        "Re-select calibration segments",
        value=False,
        help="If unchecked, uses the previously marked calibration set (if any).",
    )
    run_btn = st.form_submit_button("Run Calibration", type="primary")

if run_btn:
    from polyphony.db.connection import connect, fetchone as db_fetchone
    from polyphony.utils import build_agent_objects
    from polyphony.pipeline.calibration import mark_calibration_set
    from polyphony.pipeline.coding import run_coding_session
    from polyphony.pipeline.irr import compute_irr, find_disagreements, get_coding_matrix

    conn = connect(Path(db_path))
    project_row = db_fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
    agent_a, agent_b, _ = build_agent_objects(conn, project_id)

    with st.spinner("Marking calibration segments…"):
        n_marked = mark_calibration_set(conn, project_id, n=n_cal, clear_existing=clear_existing)
        conn.commit()
        st.write(f"Calibration set: **{n_marked}** segments.")

    progress = st.progress(0, text="Coder A coding calibration set…")
    try:
        run_id_a = run_coding_session(
            conn=conn,
            project=project_row,
            agent=agent_a,
            codebook_version_id=cb["id"],
            run_type="calibration",
        )
        progress.progress(50, text="Coder B coding calibration set…")
    except Exception as e:
        st.error(safe_error_message(e, "Coder A calibration"))
        conn.close()
        st.stop()

    try:
        run_id_b = run_coding_session(
            conn=conn,
            project=project_row,
            agent=agent_b,
            codebook_version_id=cb["id"],
            run_type="calibration",
        )
        progress.progress(90, text="Computing agreement…")
    except Exception as e:
        st.error(safe_error_message(e, "Coder B calibration"))
        conn.close()
        st.stop()

    # compute_irr saves to DB and returns metrics + disagreements
    irr = compute_irr(conn, project_id, run_id_a, run_id_b, scope="calibration",
                      notes=f"GUI calibration run. Threshold={threshold}")
    conn.commit()
    alpha = irr.get("krippendorff_alpha")
    kappa = irr.get("cohen_kappa")
    pct = irr.get("percent_agreement")
    progress.progress(100, text="Done!")

    col1, col2, col3 = st.columns(3)
    col1.metric("Krippendorff's α", f"{alpha:.3f}" if alpha is not None else "—",
                help=format_irr_label(alpha) if alpha is not None else None)
    col2.metric("Cohen's κ", f"{kappa:.3f}" if kappa is not None else "—")
    col3.metric("% Agreement", f"{pct:.1f}%" if pct is not None else "—")

    if alpha is not None and alpha >= threshold:
        st.success(
            f"Calibration passed! α = {alpha:.3f} ≥ {threshold}. "
            "You can now proceed to **Code Data**."
        )
        update_project_status(db_path, project_id, "calibrating")
    else:
        st.warning(
            f"Calibration below threshold (α = {alpha:.3f} < {threshold}). "
            "Consider reviewing your codebook and running calibration again."
        )

    # Show disagreements (already computed by compute_irr)
    disagreements = irr.get("disagreements", [])
    if disagreements:
        st.markdown(f"### Disagreements ({len(disagreements)} segments)")
        st.caption("These are segments where Coder A and Coder B assigned different codes.")
        for d in disagreements[:10]:
            seg_id = d.get("segment_id")
            seg_row = conn.execute(
                "SELECT text FROM segment WHERE id = ?", (seg_id,)
            ).fetchone()
            seg_txt = (seg_row["text"][:300] if seg_row else "(no text)") + ("…" if seg_row and len(seg_row["text"]) > 300 else "")
            codes_a = ", ".join(d.get("codes_a", [])) or "(none)"
            codes_b = ", ".join(d.get("codes_b", [])) or "(none)"

            with st.expander(f"Segment {seg_id}"):
                st.markdown(f"> {seg_txt}")
                c1, c2 = st.columns(2)
                c1.markdown(f"**Coder A:** {codes_a}")
                c2.markdown(f"**Coder B:** {codes_b}")

    conn.close()

# ── Tips ──────────────────────────────────────────────────────────────────────
with st.expander("ℹ️ Understanding calibration"):
    st.markdown("""
**Krippendorff's α** measures agreement correcting for chance. Values:
- **≥ 0.80** — acceptable for published research
- **0.60–0.79** — moderate; consider refining ambiguous codes
- **< 0.60** — low; codebook likely needs revision

**What to do when agreement is low:**
1. Review the disagreements listed above
2. Are any codes ambiguous? Tighten the inclusion/exclusion criteria (go to **Codebook**)
3. Are there codes that are rarely used? Consider removing them
4. Run calibration again after revising

You do not need perfect agreement — minor disagreements are normal and expected.
The goal is to ensure the codebook is clear enough for consistent application.
""")
