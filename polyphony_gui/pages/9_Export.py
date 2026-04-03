"""
Polyphony GUI — Export
=======================
Export codebook, assignments, audit log, and replication packages.
"""

import io
import logging
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
import pandas as pd

from polyphony_gui.components import render_sidebar, require_project
from polyphony_gui.services import safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_codebook,
    get_codes,
    update_project_status,
)

st.set_page_config(page_title="Export — Polyphony", page_icon="📦", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()
db_mtime = Path(db_path).stat().st_mtime if Path(db_path).exists() else 0.0


@st.cache_data(show_spinner=False)
def _export_codebook_bytes(db_path: str, project_id: int, fmt: str, db_mtime: float) -> bytes:
    _ = db_mtime  # cache invalidation key when DB contents change
    from polyphony.db.connection import connect
    from polyphony.io.exporters import export_codebook

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / f"codebook.{fmt}"
        conn = connect(Path(db_path))
        export_codebook(conn, project_id, out_path, format=fmt)
        conn.close()
        return out_path.read_bytes()


@st.cache_data(show_spinner=False)
def _export_assignments_bytes(db_path: str, project_id: int, fmt: str, db_mtime: float) -> bytes:
    _ = db_mtime
    from polyphony.db.connection import connect
    from polyphony.io.exporters import export_assignments

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / f"assignments.{fmt}"
        conn = connect(Path(db_path))
        export_assignments(conn, project_id, out_path, format=fmt)
        conn.close()
        return out_path.read_bytes()


@st.cache_data(show_spinner=False)
def _export_audit_log_bytes(db_path: str, project_id: int, db_mtime: float) -> bytes:
    _ = db_mtime
    from polyphony.db.connection import connect
    from polyphony.io.exporters import export_llm_log

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "audit_log.jsonl"
        conn = connect(Path(db_path))
        export_llm_log(conn, project_id, out_path)
        conn.close()
        return out_path.read_bytes()

st.title("📦 Export")
st.markdown(f"**Project:** {p['name']}")

tab_codebook, tab_assignments, tab_audit, tab_replication = st.tabs([
    "Codebook",
    "Coded Data",
    "Audit Log",
    "Replication Package",
])

# ── Codebook export ───────────────────────────────────────────────────────────
with tab_codebook:
    st.markdown("### Export Codebook")

    cb = get_codebook(db_path, project_id)
    if not cb:
        st.info("No codebook to export yet.")
    else:
        codes = get_codes(db_path, cb["id"])
        st.write(f"Codebook version **{cb['version']}** with **{len(codes)}** codes.")

        col_csv, col_json, col_yaml = st.columns(3)

        with col_csv:
            st.download_button(
                "📥 Download CSV",
                data=_export_codebook_bytes(db_path, project_id, "csv", db_mtime),
                file_name=f"{p['slug']}_codebook_v{cb['version']}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with col_json:
            st.download_button(
                "📥 Download JSON",
                data=_export_codebook_bytes(db_path, project_id, "json", db_mtime),
                file_name=f"{p['slug']}_codebook_v{cb['version']}.json",
                mime="application/json",
                use_container_width=True,
            )

        with col_yaml:
            st.download_button(
                "📥 Download YAML",
                data=_export_codebook_bytes(db_path, project_id, "yaml", db_mtime),
                file_name=f"{p['slug']}_codebook_v{cb['version']}.yaml",
                mime="text/yaml",
                use_container_width=True,
            )

        st.divider()
        st.markdown("**Preview**")
        df_cb = pd.DataFrame([{
            "Code": c["name"],
            "Level": (c.get("level") or "open").title(),
            "Description": (c.get("description") or "")[:100],
            "Inclusion": (c.get("inclusion_criteria") or "")[:80],
        } for c in codes])
        st.dataframe(df_cb, use_container_width=True, hide_index=True)

# ── Assignments export ────────────────────────────────────────────────────────
with tab_assignments:
    st.markdown("### Export Coded Data")
    st.markdown("Download all segment-code assignments with rationales and confidence scores.")

    from polyphony.db.connection import connect

    conn = connect(Path(db_path))
    run_count = conn.execute(
        "SELECT COUNT(*) AS n FROM coding_run WHERE project_id = ? AND run_type = 'independent'",
        (project_id,),
    ).fetchone()["n"]
    conn.close()

    if not run_count:
        st.info("No coding runs found. Run coding first.")
    else:
        col1, col2 = st.columns(2)

        with col1:
            st.download_button(
                "📥 Download assignments (CSV)",
                data=_export_assignments_bytes(db_path, project_id, "csv", db_mtime),
                file_name=f"{p['slug']}_assignments.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with col2:
            st.download_button(
                "📥 Download assignments (JSON)",
                data=_export_assignments_bytes(db_path, project_id, "json", db_mtime),
                file_name=f"{p['slug']}_assignments.json",
                mime="application/json",
                use_container_width=True,
            )

        # Quick preview
        with st.expander("Preview assignments"):
            conn = connect(Path(db_path))
            rows = conn.execute(
                """SELECT s.text, c.name AS code, ag.role AS coder,
                          a.confidence, a.rationale
                   FROM assignment a
                   JOIN segment s ON s.id = a.segment_id
                   JOIN code c ON c.id = a.code_id
                   JOIN coding_run r ON r.id = a.coding_run_id
                   JOIN agent ag ON ag.id = r.agent_id
                   WHERE r.project_id = ? AND r.run_type = 'independent'
                   ORDER BY a.id DESC LIMIT 50""",
                (project_id,),
            ).fetchall()
            conn.close()

            if rows:
                df = pd.DataFrame([{
                    "Segment": (r["text"][:80] + "…") if len(r["text"]) > 80 else r["text"],
                    "Code": r["code"],
                    "Coder": r["coder"].replace("_", " ").title(),
                    "Confidence": f"{r['confidence']:.2f}" if r.get("confidence") else "—",
                    "Rationale": (r.get("rationale") or "")[:60],
                } for r in rows])
                st.dataframe(df, use_container_width=True, hide_index=True)

# ── Audit log ─────────────────────────────────────────────────────────────────
with tab_audit:
    st.markdown("### LLM Audit Log")
    st.markdown(
        "Every call made to an AI model is logged with the full prompt, response, "
        "model version, temperature, seed, and token counts. "
        "This enables reproducibility and methodological transparency."
    )

    from polyphony.db.connection import connect

    conn = connect(Path(db_path))
    call_count = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_call WHERE project_id = ?", (project_id,)
    ).fetchone()["n"]
    conn.close()

    st.metric("Total LLM calls logged", call_count)

    if call_count > 0:
        st.download_button(
            "📥 Download audit log (JSONL)",
            data=_export_audit_log_bytes(db_path, project_id, db_mtime),
            file_name=f"{p['slug']}_audit_log.jsonl",
            mime="application/x-jsonlines",
            use_container_width=True,
        )

        with st.expander("Preview recent calls"):
            conn = connect(Path(db_path))
            recent = conn.execute(
                """SELECT call_type, model_name, prompt_tokens, completion_tokens,
                          duration_ms, created_at
                   FROM llm_call WHERE project_id = ?
                   ORDER BY id DESC LIMIT 20""",
                (project_id,),
            ).fetchall()
            conn.close()
            if recent:
                df_log = pd.DataFrame([{
                    "Type": r["call_type"],
                    "Model": r["model_name"],
                    "Prompt tokens": r.get("prompt_tokens") or "—",
                    "Response tokens": r.get("completion_tokens") or "—",
                    "Duration (ms)": r.get("duration_ms") or "—",
                    "Time": (r.get("created_at") or "")[:16],
                } for r in recent])
                st.dataframe(df_log, use_container_width=True, hide_index=True)

# ── Replication package ────────────────────────────────────────────────────────
with tab_replication:
    st.markdown("### Replication Package")
    st.markdown(
        "A replication package bundles everything needed for another researcher to verify "
        "your results: raw data, codebook, all assignments, the full LLM audit log, "
        "and verification scripts. Suitable for journal supplementary materials."
    )

    with st.container(border=True):
        st.markdown("**The package will include:**")
        st.markdown("""
- 📄 All imported documents (raw corpus)
- 🏷️ Final codebook (YAML + CSV)
- 📊 All coding assignments (CSV + JSON)
- 🔍 IRR results and disagreement log
- 📝 Analytical memos
- 🤖 Full LLM call log (every prompt and response)
- ✅ Verification scripts to reproduce key statistics
- 📋 README with methodology description
""")

    if st.button("Generate Replication Package", type="primary", use_container_width=True):
        with st.spinner("Building replication package… this may take a moment."):
            try:
                from polyphony.db.connection import connect
                from polyphony.io.exporters import export_replication_package

                with tempfile.TemporaryDirectory() as tmpdir:
                    out_dir = Path(tmpdir) / f"{p['slug']}_replication"
                    out_dir.mkdir()

                    conn = connect(Path(db_path))
                    export_replication_package(conn, project_id, out_dir)
                    conn.close()

                    # Zip the output directory
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for file_path in out_dir.rglob("*"):
                            if file_path.is_file():
                                zf.write(file_path, file_path.relative_to(tmpdir))
                    zip_buffer.seek(0)
                    zip_data = zip_buffer.read()

                update_project_status(db_path, project_id, "done")
                st.success("Replication package ready!")
                st.download_button(
                    "📥 Download Replication Package (.zip)",
                    data=zip_data,
                    file_name=f"{p['slug']}_replication_package.zip",
                    mime="application/zip",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(safe_error_message(e, "Replication package"))

    st.divider()
    st.caption(
        "Polyphony's replication packages are designed to meet the transparency "
        "requirements of journals in political science, sociology, and communication studies."
    )
