"""
polyphony_gui.components
========================
Shared UI components used across all pages — primarily the sidebar project
selector, page guard clause, and reusable display helpers.
"""

from __future__ import annotations

import logging

import streamlit as st

from polyphony_gui.db import list_projects, load_project

logger = logging.getLogger("polyphony_gui")


# ─── IRR helpers ──────────────────────────────────────────────────────────────

def format_irr_label(value: float | None) -> str:
    """Return a WCAG-compliant text label for an IRR alpha value."""
    if value is None:
        return "—"
    if value >= 0.80:
        return f"✅ Excellent ({value:.3f})"
    if value >= 0.67:
        return f"⚠️ Moderate ({value:.3f})"
    return f"❌ Poor ({value:.3f})"


def color_irr_value(value: float | None) -> str:
    """Return a CSS background-color string for IRR styling."""
    if value is None:
        return ""
    try:
        v = float(value)
    except (ValueError, TypeError):
        return ""
    if v >= 0.80:
        return "background-color: #d4edda"
    if v >= 0.60:
        return "background-color: #fff3cd"
    return "background-color: #f8d7da"


def style_irr_cell(val: str) -> str:
    """Style a cell containing an IRR value string (e.g. '0.823')."""
    try:
        v = float(str(val).replace("%", ""))
        if v >= 80 or (v < 1.01 and v >= 0.80):
            return "background-color: #d4edda"
        if v >= 60 or (v < 1.01 and v >= 0.60):
            return "background-color: #fff3cd"
        return "background-color: #f8d7da"
    except (ValueError, TypeError):
        return ""


# ─── Disagreement display ────────────────────────────────────────────────────

def display_disagreement(seg_id: int, seg_text: str, codes_a: str, codes_b: str,
                         asgn_a: list[dict] | None = None,
                         asgn_b: list[dict] | None = None) -> None:
    """Render a disagreement expander with segment text and coder assignments."""
    with st.expander(f"Segment {seg_id}: A={codes_a} | B={codes_b}"):
        st.markdown(f"> {seg_text}")
        st.divider()
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**Coder A:**")
            if asgn_a:
                for a in asgn_a:
                    conf = f" *(conf: {a['confidence']:.2f})*" if a.get("confidence") else ""
                    st.markdown(f"- `{a['name']}`{conf}")
                    if a.get("rationale"):
                        st.caption(a["rationale"])
            else:
                st.write(codes_a)

        with col_b:
            st.markdown("**Coder B:**")
            if asgn_b:
                for b in asgn_b:
                    conf = f" *(conf: {b['confidence']:.2f})*" if b.get("confidence") else ""
                    st.markdown(f"- `{b['name']}`{conf}")
                    if b.get("rationale"):
                        st.caption(b["rationale"])
            else:
                st.write(codes_b)


# ─── Coder run selector ──────────────────────────────────────────────────────

def build_coder_run_selector(a_runs: list[dict], b_runs: list[dict],
                             prefix: str = "irr") -> tuple[int, int]:
    """Render selectboxes for choosing Coder A and Coder B runs. Returns (run_a_id, run_b_id)."""
    col1, col2 = st.columns(2)
    with col1:
        run_a_options = {
            r["id"]: f"Coder A — Run {r['id']} ({(r.get('started_at') or '')[:10]})"
            for r in a_runs
        }
        run_a_id = st.selectbox("Coder A run", options=list(run_a_options.keys()),
                                format_func=lambda x: run_a_options[x],
                                key=f"{prefix}_run_a")
    with col2:
        run_b_options = {
            r["id"]: f"Coder B — Run {r['id']} ({(r.get('started_at') or '')[:10]})"
            for r in b_runs
        }
        run_b_id = st.selectbox("Coder B run", options=list(run_b_options.keys()),
                                format_func=lambda x: run_b_options[x],
                                key=f"{prefix}_run_b")
    return run_a_id, run_b_id


def render_sidebar() -> None:
    """Render the standard Polyphony sidebar with the active-project selector.

    Call this once near the top of every page, inside ``with st.sidebar:`` is
    NOT required — this function opens its own sidebar context internally.
    """
    with st.sidebar:
        st.markdown("## 🎼 Polyphony")
        st.caption("AI-assisted qualitative data analysis")
        st.divider()

        projects = list_projects()
        if projects:
            slugs = [p["slug"] for p in projects]
            names = {p["slug"]: p["name"] for p in projects}
            db_paths = {p["slug"]: p["db_path"] for p in projects}

            current = st.session_state.get("active_project_slug")
            if current not in slugs:
                current = slugs[0]

            selected = st.selectbox(
                "Active Project",
                options=slugs,
                format_func=lambda s: names.get(s, s),
                index=slugs.index(current) if current in slugs else 0,
                key="sidebar_project_select",
            )
            if selected != st.session_state.get("active_project_slug"):
                st.session_state.active_project_slug = selected
                st.session_state.active_project_db = db_paths[selected]
                st.session_state.active_project = load_project(db_paths[selected])
                st.rerun()

            # Ensure session state is always populated even on first load
            if st.session_state.get("active_project_slug") and not st.session_state.get("active_project"):
                slug = st.session_state.active_project_slug
                if slug in db_paths:
                    st.session_state.active_project_db = db_paths[slug]
                    st.session_state.active_project = load_project(db_paths[slug])
        else:
            st.info("No projects yet. Go to **Projects** to create one.")

        st.divider()
        st.caption("Navigate using the pages in the sidebar.")


def require_project() -> tuple[dict, str, int]:
    """Guard: stop execution if no project is active.

    Returns ``(project_row, db_path, project_id)`` when a project is active.
    Calls ``st.stop()`` otherwise, so the caller never needs to check.
    """
    project = st.session_state.get("active_project")
    db_path = st.session_state.get("active_project_db")
    if not project or not db_path:
        st.warning(
            "**No project selected.** Choose or create a project using the sidebar. "
            "If no projects appear, go to the **Projects** page to create one."
        )
        st.stop()
    return project, str(db_path), int(project["id"])
