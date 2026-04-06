"""
Polyphony GUI — Analysis
=========================
Explore code frequencies, saturation, co-occurrence, and theme synthesis.
"""

import logging
from pathlib import Path

import streamlit as st
import pandas as pd

from polyphony_gui.components import render_sidebar, require_project
from polyphony_gui.services import safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_codebook,
    update_project_status,
)

st.set_page_config(page_title="Analysis — Polyphony", page_icon="🔍", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()

st.title("🔍 Analysis")
st.markdown(f"**Project:** {p['name']}")


def _prepare_theme_context(project_row: dict, focus: str, n_themes: int) -> dict:
    """Inject user synthesis directives into the transient project context."""
    patched = dict(project_row)
    directives = [f"Target number of themes: {n_themes}"]
    if focus:
        directives.insert(0, f"Analytical focus: {focus}")

    desc = (patched.get("description") or "").strip()
    directive_block = "Theme synthesis directives:\n" + "\n".join(f"- {d}" for d in directives)
    patched["description"] = f"{desc}\n\n{directive_block}" if desc else directive_block
    return patched


def _normalize_synthesis_result(result: object, focus: str, n_themes: int) -> object:
    """Ensure the rendered result reflects synthesis settings chosen in the UI."""
    if isinstance(result, dict):
        normalized = dict(result)
        themes = normalized.get("themes")
        if isinstance(themes, list):
            normalized["themes"] = themes[:n_themes]
        normalized["target_theme_count"] = int(n_themes)
        if focus:
            normalized["analytical_focus"] = focus
        return normalized

    if isinstance(result, str):
        header_parts = [f"Target themes: {int(n_themes)}"]
        if focus:
            header_parts.insert(0, f"Analytical focus: {focus}")
        return f"{' | '.join(header_parts)}\n\n{result}"

    return result

tab_freq, tab_saturation, tab_cooccurrence, tab_themes = st.tabs([
    "Code Frequencies",
    "Theoretical Saturation",
    "Co-occurrence",
    "Theme Synthesis",
])

# ── Code frequencies ──────────────────────────────────────────────────────────
with tab_freq:
    st.markdown("### Code Frequency Table")
    st.markdown("How often does each code appear across the corpus?")

    from polyphony.db.connection import connect
    from polyphony.pipeline.analysis import code_frequency_table

    conn = connect(Path(db_path))
    freq_rows = code_frequency_table(conn, project_id)
    conn.close()

    if not freq_rows:
        st.info("No coding data yet. Run coding first.")
    else:
        df = pd.DataFrame([{
            "Code": r["code_name"],
            "Level": r.get("level", "?").title(),
            "Segments": r["segment_count"],
            "Assignments": r["assignment_count"],
            "Description": (r.get("description") or "")[:80],
        } for r in freq_rows])

        st.dataframe(df, use_container_width=True, hide_index=True)

        # Bar chart
        import plotly.express as px
        fig = px.bar(
            df.head(20),
            x="Code",
            y="Segments",
            color="Level",
            title="Top 20 Codes by Segment Count",
            color_discrete_map={
                "Open": "#4F46E5",
                "Axial": "#7C3AED",
                "Selective": "#A78BFA",
            },
        )
        fig.update_layout(xaxis_tickangle=-45, height=400)
        st.plotly_chart(fig, use_container_width=True)

        # Pie chart
        fig2 = px.pie(
            df,
            names="Code",
            values="Segments",
            title="Code Distribution",
        )
        fig2.update_traces(textposition="inside", textinfo="percent+label")
        fig2.update_layout(height=400)
        st.plotly_chart(fig2, use_container_width=True)

# ── Saturation ────────────────────────────────────────────────────────────────
with tab_saturation:
    st.markdown("### Theoretical Saturation")
    st.markdown(
        "Saturation is reached when new segments no longer introduce new codes. "
        "The chart below shows how many new codes emerged as more segments were coded."
    )

    from polyphony.db.connection import connect
    from polyphony.pipeline.analysis import check_saturation

    conn = connect(Path(db_path))
    try:
        sat = check_saturation(conn, project_id)
        conn.close()

        windows_data = sat.get("new_codes_per_window", [])
        if sat and windows_data:
            df_sat = pd.DataFrame([
                {"window_index": i + 1, "new_codes": n}
                for i, n in enumerate(windows_data)
            ])

            import plotly.express as px
            fig = px.line(
                df_sat,
                x="window_index",
                y="new_codes",
                title="New Codes per Segment Window",
                labels={"window_index": "Window", "new_codes": "New Codes Emerged"},
                markers=True,
            )
            fig.add_hline(y=0, line_dash="dash", line_color="green",
                          annotation_text="Saturation (no new codes)")
            fig.update_layout(height=350)
            st.plotly_chart(fig, use_container_width=True)

            saturated = sat.get("likely_saturated", False)
            if saturated:
                st.success("Data appears theoretically saturated — the last three windows produced no new codes.")
            else:
                st.warning("Saturation not yet reached — new codes are still emerging.")

            st.metric("Total unique codes observed", sat.get("total_unique_codes", "—"))
        else:
            st.info("Not enough coding data for saturation analysis.")
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        st.error(safe_error_message(e, "Saturation analysis"))

# ── Co-occurrence ─────────────────────────────────────────────────────────────
with tab_cooccurrence:
    st.markdown("### Code Co-occurrence")
    st.markdown(
        "Which codes tend to appear together on the same segment? "
        "High co-occurrence may indicate conceptual overlap or a relationship worth exploring."
    )

    from polyphony.db.connection import connect
    from polyphony.pipeline.analysis import co_occurrence_matrix

    conn = connect(Path(db_path))
    try:
        matrix = co_occurrence_matrix(conn, project_id)
        conn.close()

        if matrix:
            # matrix is a nested dict: {code_a: {code_b: count}}
            codes_list = sorted(matrix.keys())

            if len(codes_list) > 1:
                import plotly.graph_objects as go
                import numpy as np

                # Build a symmetric matrix from the nested dict
                np_matrix = np.zeros((len(codes_list), len(codes_list)), dtype=int)
                for i, ca in enumerate(codes_list):
                    for j, cb in enumerate(codes_list):
                        if ca in matrix and cb in matrix[ca]:
                            np_matrix[i][j] = matrix[ca][cb]

                fig = go.Figure(data=go.Heatmap(
                    z=np_matrix,
                    x=codes_list,
                    y=codes_list,
                    colorscale="Blues",
                    text=np_matrix,
                    texttemplate="%{text}",
                ))
                fig.update_layout(
                    title="Code Co-occurrence Matrix",
                    height=max(400, 30 * len(codes_list)),
                    xaxis_tickangle=-45,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough codes for a co-occurrence matrix.")
        else:
            st.info("No coding data available for co-occurrence analysis.")
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        st.error(safe_error_message(e, "Co-occurrence analysis"))

# ── Theme synthesis ───────────────────────────────────────────────────────────
with tab_themes:
    st.markdown("### Theme Synthesis")
    st.markdown(
        "Ask an AI to synthesize emerging themes from the coded data and write a "
        "narrative summary. This is a starting point for your own interpretation."
    )

    cb = get_codebook(db_path, project_id)
    if not cb:
        st.info("No codebook found.")
        st.stop()

    with st.form("synthesis_form"):
        focus = st.text_area(
            "Analytical focus (optional)",
            placeholder="e.g. 'Focus on how participants describe coping with housing insecurity.'",
            height=80,
        )
        n_themes = st.slider("Target number of themes", min_value=2, max_value=10, value=4)
        synth_btn = st.form_submit_button("Generate Theme Synthesis", type="primary")

    if synth_btn:
        from polyphony.db.connection import connect, fetchone as db_fetchone
        from polyphony.utils import build_agent_objects
        from polyphony.pipeline.analysis import synthesize_themes
        from polyphony_gui.db import get_codebook as _get_cb

        _cb = _get_cb(db_path, project_id)
        if not _cb:
            st.error("No codebook found.")
            st.stop()

        conn = connect(Path(db_path))
        project_row = db_fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
        _, _, supervisor = build_agent_objects(conn, project_id)
        focus_value = focus.strip()

        with st.spinner("Synthesizing themes…"):
            try:
                # synthesize_themes(agent, conn, project, codebook_version_id) -> str
                project_for_synthesis = _prepare_theme_context(
                    project_row,
                    focus_value,
                    int(n_themes),
                )
                result = synthesize_themes(
                    supervisor,
                    conn,
                    project_for_synthesis,
                    _cb["id"],
                )
                st.session_state["last_synthesis"] = _normalize_synthesis_result(
                    result,
                    focus_value,
                    int(n_themes),
                )
                st.session_state["last_synthesis_meta"] = {
                    "focus": focus_value,
                    "target_theme_count": int(n_themes),
                }
                update_project_status(db_path, project_id, "analyzing")
            except Exception as e:
                st.error(safe_error_message(e, "Theme synthesis"))
        conn.close()

    if "last_synthesis" in st.session_state:
        result = st.session_state["last_synthesis"]
        st.divider()
        st.markdown("### Synthesized Themes")
        synthesis_meta = st.session_state.get("last_synthesis_meta", {})
        if synthesis_meta.get("focus"):
            st.caption(
                f"Focus: {synthesis_meta['focus']} | "
                f"Target themes: {synthesis_meta.get('target_theme_count', '—')}"
            )
        else:
            st.caption(f"Target themes: {synthesis_meta.get('target_theme_count', '—')}")

        themes = result.get("themes", []) if isinstance(result, dict) else []
        if isinstance(result, str):
            st.markdown(result)
        elif themes:
            for i, theme in enumerate(themes, 1):
                name = theme.get("name", f"Theme {i}")
                desc = theme.get("description", "")
                codes = theme.get("codes", [])
                with st.expander(f"**Theme {i}: {name}**"):
                    if desc:
                        st.markdown(desc)
                    if codes:
                        st.markdown("**Related codes:** " + ", ".join(f"`{c}`" for c in codes))
        else:
            st.write(result)

        if st.button("Save synthesis as memo"):
            from polyphony_gui.db import add_memo
            import json as json_lib
            content = json_lib.dumps(st.session_state["last_synthesis"], indent=2) if isinstance(st.session_state["last_synthesis"], dict) else str(st.session_state["last_synthesis"])
            add_memo(db_path, project_id, "Theme Synthesis", content, memo_type="synthesis")
            st.success("Saved as memo.")
