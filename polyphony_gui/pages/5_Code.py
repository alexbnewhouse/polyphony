"""
Polyphony GUI — Code Data
==========================
Run independent coding sessions where both AI coders process the full corpus.
"""

import logging
from pathlib import Path

import streamlit as st

from polyphony_gui.components import render_sidebar, require_project
from polyphony_gui.services import safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_codebook,
    get_codes,
    get_coding_runs,
    update_project_status,
)

st.set_page_config(page_title="Code Data — Polyphony", page_icon="🤖", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()

st.title("🤖 Code Data")
st.markdown(f"**Project:** {p['name']}")

cb = get_codebook(db_path, project_id)
if not cb:
    st.warning("No codebook found. Please create a codebook first.")
    st.stop()

codes = get_codes(db_path, cb["id"])
if not codes:
    st.warning("The codebook has no active codes.")
    st.stop()

st.markdown(
    f"Using codebook **version {cb['version']}** with **{len(codes)}** codes. "
    "Both AI coders will independently code every segment of your corpus."
)

# ── Previous runs ─────────────────────────────────────────────────────────────
runs = get_coding_runs(db_path, project_id)
ind_runs = [r for r in runs if r.get("run_type") == "independent"]

if ind_runs:
    st.markdown("### Coding Runs")
    import pandas as pd

    rows = []
    for r in ind_runs:
        rows.append({
            "Run ID": r["id"],
            "Coder": r.get("agent_role", "?").replace("_", " ").title(),
            "Model": r.get("model_name", "?"),
            "Status": r.get("status", "?").title(),
            "Segments": r.get("segment_count") or "—",
            "Started": (r.get("started_at") or "")[:16],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Show assignments sample
    with st.expander("View a sample of coded segments"):
        from polyphony.db.connection import connect, fetchall as db_fetchall

        conn = connect(Path(db_path))
        sample = db_fetchall(
            conn,
            """SELECT s.text, c.name AS code_name, a.confidence, a.rationale,
                      ag.role AS coder_role
               FROM assignment a
               JOIN segment s ON s.id = a.segment_id
               JOIN code c ON c.id = a.code_id
               JOIN coding_run r ON r.id = a.coding_run_id
               JOIN agent ag ON ag.id = r.agent_id
               WHERE r.project_id = ? AND r.run_type = 'independent'
               ORDER BY a.id DESC LIMIT 20""",
            (project_id,),
        )
        conn.close()

        for row in sample:
            txt = row["text"][:200] + ("…" if len(row["text"]) > 200 else "")
            st.markdown(
                f"**{row['coder_role'].replace('_',' ').title()}** → `{row['code_name']}`"
                + (f" *(conf: {row['confidence']:.2f})*" if row.get("confidence") else "")
            )
            st.caption(txt)
            if row.get("rationale"):
                st.write(f"Rationale: {row['rationale']}")
            st.divider()

# ── Run coding ────────────────────────────────────────────────────────────────
st.divider()
st.markdown("### Run Independent Coding")

with st.form("coding_form"):
    coder_choice = st.radio(
        "Which coders to run?",
        options=["both", "coder_a", "coder_b"],
        format_func=lambda x: {
            "both": "Both coders (recommended)",
            "coder_a": "Coder A only",
            "coder_b": "Coder B only",
        }[x],
    )
    prompt_key = st.selectbox(
        "Coding approach",
        options=["open_coding", "deductive_coding"],
        format_func=lambda x: {
            "open_coding": "Open coding — coders apply any relevant codes",
            "deductive_coding": "Deductive coding — strictly apply existing codebook only",
        }[x],
    )
    resume = st.checkbox(
        "Resume interrupted run (skip already-coded segments)",
        value=False,
    )
    run_btn = st.form_submit_button("Start Coding", type="primary")

if run_btn:
    from polyphony.db.connection import connect, fetchone as db_fetchone, fetchall as db_fetchall
    from polyphony.utils import build_agent_objects
    from polyphony.pipeline.coding import run_coding_session

    conn = connect(Path(db_path))
    project_row = db_fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
    agent_a, agent_b, _ = build_agent_objects(conn, project_id)

    n_segs = db_fetchone(
        conn, "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?", (project_id,)
    )["n"]
    st.write(f"Total segments to code: **{n_segs}**")

    completed_runs = []

    if coder_choice in ("both", "coder_a"):
        progress_a = st.progress(0, text="Coder A is coding…")
        try:
            run_id_a = run_coding_session(
                conn=conn,
                project=project_row,
                agent=agent_a,
                codebook_version_id=cb["id"],
                run_type="independent",
                resume=resume,
                prompt_key=prompt_key,
            )
            completed_runs.append(("Coder A", run_id_a))
            progress_a.progress(100, text="Coder A done!")
        except Exception as e:
            st.error(safe_error_message(e, "Coder A coding"))
            conn.close()
            if coder_choice == "coder_a":
                st.stop()

    if coder_choice in ("both", "coder_b"):
        progress_b = st.progress(0, text="Coder B is coding…")
        try:
            run_id_b = run_coding_session(
                conn=conn,
                project=project_row,
                agent=agent_b,
                codebook_version_id=cb["id"],
                run_type="independent",
                resume=resume,
                prompt_key=prompt_key,
            )
            completed_runs.append(("Coder B", run_id_b))
            progress_b.progress(100, text="Coder B done!")
        except Exception as e:
            st.error(safe_error_message(e, "Coder B coding"))

    conn.commit()

    if completed_runs:
        st.success(
            "Coding complete for: " + ", ".join(name for name, _ in completed_runs) + ". "
            "Proceed to **IRR Dashboard** to measure agreement."
        )
        update_project_status(db_path, project_id, "coding")

    conn.close()
    st.rerun()

with st.expander("ℹ️ About independent coding"):
    st.markdown("""
**Why run both coders independently?**

The core methodology of polyphony replicates multi-researcher studies. Each coder works
in isolation — they see only the codebook and the segment, never the other coder's decisions.
This preserves the independence required for valid inter-rater reliability (IRR) scores.

**How long does it take?**

It depends on your corpus size and the AI model. Expect roughly 2–5 seconds per segment
per coder for cloud models (GPT-4o, Claude) and 5–30 seconds for local models (Ollama).
A corpus of 100 segments with both coders takes 5–15 minutes.

**Can I resume an interrupted run?**

Yes — check "Resume interrupted run" before starting. Polyphony tracks which segments
have already been coded and skips them.
""")
