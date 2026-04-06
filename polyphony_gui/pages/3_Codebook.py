"""
Polyphony GUI — Codebook
=========================
Manage codes: run AI induction, add/edit/delete codes, view the codebook.
"""

import json
import logging
from pathlib import Path

import streamlit as st

from polyphony_gui.components import render_sidebar, require_project
from polyphony_gui.services import validate_codebook_rows, safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_codebook,
    get_codes,
    save_codebook_from_candidates,
    update_project_status,
)

st.set_page_config(page_title="Codebook — Polyphony", page_icon="🏷️", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()

# ─── Page ─────────────────────────────────────────────────────────────────────
st.title("🏷️ Codebook")
st.markdown(f"**Project:** {p['name']}")

cb = get_codebook(db_path, project_id)

tab_view, tab_induce, tab_manual, tab_import_csv = st.tabs([
    "View Codebook",
    "AI Induction",
    "Add Code Manually",
    "Import from CSV/YAML",
])

# ── View codebook ─────────────────────────────────────────────────────────────
with tab_view:
    if not cb:
        st.info(
            "No codebook yet. Use **AI Induction** to generate codes from your data, "
            "or **Add Code Manually** to create codes yourself."
        )
    else:
        codes = get_codes(db_path, cb["id"])
        st.markdown(f"**Version {cb['version']}** — Stage: {cb.get('stage', '?').title()}")
        if cb.get("rationale"):
            st.caption(cb["rationale"])

        if not codes:
            st.warning("This codebook version has no active codes.")
        else:
            # Group by level
            levels = ["open", "axial", "selective"]
            level_labels = {"open": "Open Codes", "axial": "Axial Codes", "selective": "Selective Codes"}

            for level in levels:
                level_codes = [c for c in codes if c.get("level") == level]
                if not level_codes:
                    continue
                st.markdown(f"#### {level_labels.get(level, level.title())}")
                for code in level_codes:
                    with st.expander(f"**{code['name']}**"):
                        if code.get("description"):
                            st.markdown(f"*{code['description']}*")
                        c1, c2 = st.columns(2)
                        with c1:
                            if code.get("inclusion_criteria"):
                                st.markdown("**Include when:**")
                                st.write(code["inclusion_criteria"])
                        with c2:
                            if code.get("exclusion_criteria"):
                                st.markdown("**Do not include when:**")
                                st.write(code["exclusion_criteria"])
                        quotes = json.loads(code.get("example_quotes") or "[]")
                        if quotes:
                            st.markdown("**Example quotes:**")
                            for q in quotes[:3]:
                                st.markdown(f"> {q}")

            # Allow editing a code
            st.divider()
            st.markdown("**Edit or deactivate a code**")
            code_names = [c["name"] for c in codes]
            selected_code_name = st.selectbox("Select code to edit", options=["— choose —"] + code_names)

            if selected_code_name and selected_code_name != "— choose —":
                sel_code = next(c for c in codes if c["name"] == selected_code_name)
                with st.form(f"edit_code_{sel_code['id']}"):
                    new_name = st.text_input("Name", value=sel_code["name"])
                    new_desc = st.text_area("Description", value=sel_code.get("description") or "")
                    new_inc = st.text_area("Inclusion criteria", value=sel_code.get("inclusion_criteria") or "")
                    new_exc = st.text_area("Exclusion criteria", value=sel_code.get("exclusion_criteria") or "")
                    new_level = st.selectbox(
                        "Level",
                        options=["open", "axial", "selective"],
                        index=["open", "axial", "selective"].index(sel_code.get("level", "open")),
                    )
                    col_save, col_delete = st.columns(2)
                    save_btn = col_save.form_submit_button("Save Changes", type="primary")
                    delete_btn = col_delete.form_submit_button("Deactivate Code")

                if save_btn:
                    from polyphony.db.connection import connect
                    conn = connect(Path(db_path))
                    conn.execute(
                        "UPDATE code SET name=?, description=?, inclusion_criteria=?, "
                        "exclusion_criteria=?, level=? WHERE id=?",
                        (new_name, new_desc, new_inc, new_exc, new_level, sel_code["id"]),
                    )
                    conn.commit()
                    conn.close()
                    st.success("Code updated.")
                    st.rerun()

                if delete_btn:
                    from polyphony.db.connection import connect
                    conn = connect(Path(db_path))
                    conn.execute("UPDATE code SET is_active=0 WHERE id=?", (sel_code["id"],))
                    conn.commit()
                    conn.close()
                    st.success(f"Code '{sel_code['name']}' deactivated.")
                    st.rerun()

# ── AI induction ──────────────────────────────────────────────────────────────
with tab_induce:
    st.markdown("### Generate Codes with AI")
    st.markdown(
        "Polyphony will have both AI coders analyze a sample of your documents and "
        "suggest codes. You then review, edit, and approve them."
    )

    from polyphony_gui.db import get_documents
    docs = get_documents(db_path, project_id)
    if not docs:
        st.warning("Please import documents first (go to **Import Data**).")
        st.stop()

    with st.form("induction_form"):
        sample_size = st.slider(
            "Sample size",
            min_value=5,
            max_value=50,
            value=20,
            help="Number of segments the AI will analyze to suggest codes. "
                 "Larger sample = more thorough, but slower.",
        )
        skip_coder_b = st.checkbox(
            "Use only one AI coder for induction (faster)",
            value=False,
        )
        run_btn = st.form_submit_button("Generate Code Suggestions", type="primary")

    if run_btn:
        from polyphony.db.connection import connect, fetchone as db_fetchone, insert as db_insert2
        from polyphony.utils import build_agent_objects
        from polyphony.pipeline.induction import (
            select_induction_sample,
            run_agent_induction,
            merge_candidates,
        )

        conn = connect(Path(db_path))
        project_row = db_fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
        agent_a, agent_b, _ = build_agent_objects(conn, project_id)

        with st.spinner("Sampling segments…"):
            segments = select_induction_sample(conn, project_id, n=sample_size, seed=42)
            st.write(f"Sampled **{len(segments)}** segments from {len({s['document_id'] for s in segments})} documents.")

        # Create placeholder codebook for induction run
        cb_existing = db_fetchone(
            conn,
            "SELECT id FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        )
        if cb_existing:
            cb_id = cb_existing["id"]
        else:
            cb_id = db_insert2(conn, "codebook_version", {
                "project_id": project_id,
                "version": 0,
                "stage": "draft",
                "rationale": "Placeholder for induction run",
            })
            conn.commit()

        run_id_a = db_insert2(conn, "coding_run", {
            "project_id": project_id,
            "codebook_version_id": cb_id,
            "agent_id": agent_a.agent_id,
            "run_type": "induction",
            "status": "running",
            "started_at": None,
            "segment_count": len(segments),
        })
        conn.commit()

        with st.spinner("AI Coder A is analyzing segments…"):
            try:
                candidates_a = run_agent_induction(agent_a, segments, project_row, run_id_a, conn)
                st.write(f"Coder A suggested **{len(candidates_a)}** codes.")
            except Exception as e:
                st.error(safe_error_message(e, "Coder A induction"))
                conn.close()
                st.stop()

        candidates_b = []
        if not skip_coder_b:
            run_id_b = db_insert2(conn, "coding_run", {
                "project_id": project_id,
                "codebook_version_id": cb_id,
                "agent_id": agent_b.agent_id,
                "run_type": "induction",
                "status": "running",
                "started_at": None,
                "segment_count": len(segments),
            })
            conn.commit()
            with st.spinner("AI Coder B is analyzing segments…"):
                try:
                    candidates_b = run_agent_induction(agent_b, segments, project_row, run_id_b, conn)
                    st.write(f"Coder B suggested **{len(candidates_b)}** codes.")
                except Exception as e:
                    st.warning(safe_error_message(e, "Coder B induction"))

        conn.close()

        merged = merge_candidates(candidates_a, candidates_b) if candidates_b else candidates_a
        st.session_state["induction_candidates"] = merged
        st.success(f"Generated **{len(merged)}** unique candidate codes. Review them below.")

    # Review candidates
    if "induction_candidates" in st.session_state and st.session_state["induction_candidates"]:
        candidates = st.session_state["induction_candidates"]
        st.divider()
        st.markdown("### Review AI-Suggested Codes")
        st.markdown(
            "Edit any code name or description, then uncheck codes you don't want. "
            "Click **Save Codebook** when you're satisfied."
        )

        reviewed = []
        for i, code in enumerate(candidates):
            with st.expander(f"**{code.get('name', f'Code {i+1}')}**", expanded=False):
                keep = st.checkbox("Include this code", value=True, key=f"keep_{i}")
                col1, col2 = st.columns(2)
                with col1:
                    name_val = st.text_input("Name", value=code.get("name", ""), key=f"name_{i}")
                    level_val = st.selectbox(
                        "Level",
                        options=["open", "axial", "selective"],
                        index=["open", "axial", "selective"].index(code.get("level", "open")),
                        key=f"level_{i}",
                    )
                with col2:
                    desc_val = st.text_area("Description", value=code.get("description", ""), key=f"desc_{i}", height=80)
                inc_val = st.text_area("Inclusion criteria", value=code.get("inclusion_criteria", ""), key=f"inc_{i}", height=60)
                exc_val = st.text_area("Exclusion criteria", value=code.get("exclusion_criteria", ""), key=f"exc_{i}", height=60)

                if keep:
                    reviewed.append({
                        "name": name_val,
                        "description": desc_val,
                        "inclusion_criteria": inc_val,
                        "exclusion_criteria": exc_val,
                        "level": level_val,
                        "example_quotes": code.get("example_quotes", []),
                    })

        if st.button("Save Codebook", type="primary"):
            approved = [r for r in reviewed if r["name"].strip()]
            if not approved:
                st.error("No codes to save. Make sure at least one code is included and has a name.")
            else:
                cb_id = save_codebook_from_candidates(db_path, project_id, approved)
                update_project_status(db_path, project_id, "inducing")
                del st.session_state["induction_candidates"]
                st.success(f"Saved codebook with **{len(approved)}** codes. Proceed to **Calibrate**.")
                st.rerun()

# ── Manual code entry ─────────────────────────────────────────────────────────
with tab_manual:
    st.markdown("### Add a Code Manually")

    if not cb:
        st.info("No codebook yet. You can start one by adding codes here.")

    with st.form("add_code_form"):
        code_name = st.text_input("Code name *", placeholder="e.g. HOUSING_INSECURITY")
        code_desc = st.text_area("Description", placeholder="What does this code capture?", height=80)
        code_inc = st.text_area("Inclusion criteria", placeholder="Apply this code when…", height=60)
        code_exc = st.text_area("Exclusion criteria", placeholder="Do NOT apply this code when…", height=60)
        code_level = st.selectbox("Level", options=["open", "axial", "selective"])
        add_btn = st.form_submit_button("Add Code", type="primary")

    if add_btn:
        if not code_name.strip():
            st.error("Code name is required.")
        else:
            from polyphony.db.connection import connect, insert as db_insert, fetchone as db_fetchone
            conn = connect(Path(db_path))

            # Get or create a codebook version
            existing_cb = db_fetchone(
                conn,
                "SELECT * FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (project_id,),
            )
            if existing_cb and existing_cb["stage"] in ("draft", "calibrated"):
                cb_version_id = existing_cb["id"]
            else:
                last_v = existing_cb["version"] if existing_cb else 0
                cb_version_id = db_insert(conn, "codebook_version", {
                    "project_id": project_id,
                    "version": last_v + 1,
                    "stage": "draft",
                    "rationale": "Manually created",
                })
                conn.commit()

            max_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m FROM code WHERE codebook_version_id = ?",
                (cb_version_id,),
            ).fetchone()["m"]

            db_insert(conn, "code", {
                "project_id": project_id,
                "codebook_version_id": cb_version_id,
                "name": code_name.strip(),
                "description": code_desc.strip(),
                "inclusion_criteria": code_inc.strip(),
                "exclusion_criteria": code_exc.strip(),
                "example_quotes": "[]",
                "level": code_level,
                "sort_order": max_order + 1,
                "is_active": 1,
            })
            conn.commit()
            conn.close()
            st.success(f"Code **{code_name.strip()}** added.")
            st.rerun()

# ── Import CSV/YAML ───────────────────────────────────────────────────────────
with tab_import_csv:
    st.markdown("### Import Codebook from CSV or YAML")
    st.markdown(
        "Upload a CSV or YAML file with fields: `name`, `description`, "
        "`inclusion_criteria`, `exclusion_criteria`, `level` (open/axial/selective)."
    )

    uploaded_cb = st.file_uploader("Upload codebook file", type=["csv", "yaml", "yml"])

    if uploaded_cb:
        import pandas as pd
        import io

        def _rows_to_candidates(rows: list[dict]) -> list[dict]:
            candidates = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                candidates.append({
                    "name": str(row.get("name", "")).strip(),
                    "description": str(row.get("description", "")).strip(),
                    "inclusion_criteria": str(row.get("inclusion_criteria", "")).strip(),
                    "exclusion_criteria": str(row.get("exclusion_criteria", "")).strip(),
                    "level": str(row.get("level", "open")).strip(),
                    "example_quotes": [],
                })
            return [c for c in candidates if c["name"]]

        try:
            file_name = (uploaded_cb.name or "").lower()
            raw = uploaded_cb.read()
            parsed_rows: list[dict] = []

            if file_name.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(raw))
                st.dataframe(df.head(10), use_container_width=True, hide_index=True)
                # Case-insensitive column matching
                df.columns = [c.lower().strip() for c in df.columns]
                if "name" not in df.columns:
                    st.error("CSV must have a 'name' column (case-insensitive).")
                else:
                    parsed_rows = df.to_dict(orient="records")
            else:
                import yaml

                parsed = yaml.safe_load(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    parsed_rows = parsed.get("codes", []) if isinstance(parsed.get("codes"), list) else []
                elif isinstance(parsed, list):
                    parsed_rows = parsed
                else:
                    parsed_rows = []

                preview_df = pd.DataFrame(parsed_rows) if parsed_rows else pd.DataFrame()
                if not preview_df.empty:
                    st.dataframe(preview_df.head(10), use_container_width=True, hide_index=True)
                else:
                    st.info("No preview rows found. YAML should be a list of code objects, or `{codes: [...]}`.")

            candidates = _rows_to_candidates(parsed_rows)

            # P0: Validate codebook rows before import
            if candidates:
                validation_err = validate_codebook_rows(candidates)
                if validation_err:
                    st.warning(f"Validation issue: {validation_err}")

            if st.button("Import this codebook", type="primary"):
                if not parsed_rows:
                    st.error("No rows found in the uploaded file.")
                else:
                    if not candidates:
                        st.error("No valid codes found. Each code must include a non-empty `name`.")
                    else:
                        save_codebook_from_candidates(
                            db_path, project_id, candidates, rationale="Imported from file"
                        )
                        st.success(f"Imported **{len(candidates)}** codes.")
                        st.rerun()
        except Exception as e:
            st.error(safe_error_message(e, "Codebook import"))
