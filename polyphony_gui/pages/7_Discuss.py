"""
Polyphony GUI — Discuss
========================
Review and resolve coding disagreements and flagged cases.
"""

import logging
from pathlib import Path

import streamlit as st

from polyphony_gui.components import render_sidebar, require_project, display_disagreement

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    get_flags,
    resolve_flag,
    add_memo,
    update_project_status,
)

st.set_page_config(page_title="Discuss — Polyphony", page_icon="💬", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()


def _add_supervisor_flag_note(db_path: str, project_id: int, flag_id: int, content: str) -> None:
    from polyphony.db.connection import connect, insert as db_insert, fetchone as db_fetchone

    conn = connect(Path(db_path))
    supervisor = db_fetchone(
        conn,
        "SELECT id FROM agent WHERE project_id = ? AND role = 'supervisor'",
        (project_id,),
    )
    db_insert(conn, "discussion_turn", {
        "flag_id": flag_id,
        "agent_id": supervisor["id"] if supervisor else None,
        "role": "supervisor",
        "content": content,
    })
    conn.commit()
    conn.close()

st.title("💬 Discuss")
st.markdown(f"**Project:** {p['name']}")

tab_flags, tab_disagreements, tab_memos = st.tabs(["Open Flags", "Disagreements", "Memos"])

# ── Flags ─────────────────────────────────────────────────────────────────────
with tab_flags:
    st.markdown("### Flagged Cases")
    st.markdown(
        "During coding, AI coders can flag segments they find ambiguous, where they see "
        "code overlap, or where they have low confidence. Review these flags here."
    )

    flags = get_flags(db_path, project_id, status="open")

    if not flags:
        st.success("No open flags — all cases have been resolved.")
    else:
        st.write(f"**{len(flags)}** open flag(s)")

        from polyphony.db.connection import connect, fetchall as db_fetchall

        flag_ids = [f["id"] for f in flags]
        turns_by_flag: dict[int, list[dict]] = {}
        if flag_ids:
            placeholders = ",".join("?" for _ in flag_ids)
            conn = connect(Path(db_path))
            turns = db_fetchall(
                conn,
                f"SELECT * FROM discussion_turn WHERE flag_id IN ({placeholders}) ORDER BY flag_id, id",
                tuple(flag_ids),
            )
            conn.close()
            for turn in turns:
                turns_by_flag.setdefault(turn["flag_id"], []).append(turn)

        FLAG_TYPE_LABELS = {
            "ambiguous_segment": "⚠️ Ambiguous Segment",
            "code_overlap": "🔀 Code Overlap",
            "missing_code": "❓ Possible Missing Code",
            "low_confidence": "📉 Low Confidence",
        }

        for flag in flags:
            flag_label = FLAG_TYPE_LABELS.get(flag.get("flag_type"), flag.get("flag_type", "Flag"))
            seg_txt = flag.get("segment_text", "")
            display_txt = (seg_txt[:300] + "…") if len(seg_txt) > 300 else seg_txt

            with st.expander(f"{flag_label} — Segment {flag['segment_id']}"):
                st.markdown(f"> {display_txt}")

                if flag.get("note"):
                    st.info(f"Coder note: {flag['note']}")

                turns = turns_by_flag.get(flag["id"], [])

                if turns:
                    for t in turns:
                        role = t.get("role", "unknown")
                        content = t.get("content", "")
                        st.markdown(f"**{role.title()}:** {content}")

                st.divider()
                with st.form(f"resolve_flag_{flag['id']}"):
                    resolution = st.text_area(
                        "Your decision / resolution",
                        placeholder="e.g. 'Apply code X here because…' or 'This is not codeable.'",
                    )
                    add_note_only = st.checkbox("Add note only (don't resolve yet)", value=False)
                    col_res, col_memo = st.columns(2)
                    resolve_btn = col_res.form_submit_button("Resolve Flag", type="primary")
                    note_btn = col_memo.form_submit_button("Add Note")

                if resolve_btn and resolution.strip():
                    if add_note_only:
                        _add_supervisor_flag_note(db_path, project_id, flag["id"], resolution.strip())
                        st.success("Note added.")
                    else:
                        resolve_flag(db_path, flag["id"], resolution.strip())
                        st.success("Flag resolved.")
                    update_project_status(db_path, project_id, "discussing")
                    st.rerun()
                elif resolve_btn:
                    st.error("Please enter a resolution before resolving.")

                if note_btn and resolution.strip():
                    _add_supervisor_flag_note(db_path, project_id, flag["id"], resolution.strip())
                    update_project_status(db_path, project_id, "discussing")
                    st.success("Note added.")
                    st.rerun()
                elif note_btn:
                    st.error("Please enter a note before saving.")

# ── Disagreements ─────────────────────────────────────────────────────────────
with tab_disagreements:
    st.markdown("### Coding Disagreements")
    st.markdown(
        "These segments were coded differently by Coder A and Coder B. "
        "Review each one and decide on the final code assignment."
    )

    from polyphony.db.connection import connect, fetchall as db_fetchall

    conn = connect(Path(db_path))

    # Get latest independent run pair
    latest_runs = db_fetchall(
        conn,
        """SELECT r.*, a.role AS agent_role
           FROM coding_run r
           JOIN agent a ON a.id = r.agent_id
           WHERE r.project_id = ? AND r.run_type = 'independent' AND r.status = 'complete'
           ORDER BY r.id DESC""",
        (project_id,),
    )
    a_runs = [r for r in latest_runs if r.get("agent_role") == "coder_a"]
    b_runs = [r for r in latest_runs if r.get("agent_role") == "coder_b"]

    if not a_runs or not b_runs:
        conn.close()
        st.info("No completed independent coding runs found. Run coding first.")
    else:
        run_a = a_runs[0]
        run_b = b_runs[0]

        from polyphony.pipeline.irr import find_disagreements

        disagreements = find_disagreements(conn, run_a["id"], run_b["id"])
        conn.close()

        if not disagreements:
            st.success("No disagreements found! Perfect or near-perfect agreement.")
        else:
            st.write(f"**{len(disagreements)}** disagreement(s)")

            seg_ids = [d.get("segment_id") for d in disagreements if d.get("segment_id") is not None]
            seg_by_id: dict[int, dict] = {}
            asgn_a_by_seg: dict[int, list[dict]] = {}
            asgn_b_by_seg: dict[int, list[dict]] = {}

            if seg_ids:
                placeholders = ",".join("?" for _ in seg_ids)
                conn2 = connect(Path(db_path))
                seg_rows = db_fetchall(
                    conn2,
                    f"SELECT id, text FROM segment WHERE id IN ({placeholders})",
                    tuple(seg_ids),
                )
                asgn_a_rows = db_fetchall(
                    conn2,
                    f"""SELECT a.segment_id, c.name, a.confidence, a.rationale
                       FROM assignment a JOIN code c ON c.id = a.code_id
                       WHERE a.coding_run_id = ? AND a.segment_id IN ({placeholders})""",
                    (run_a["id"], *seg_ids),
                )
                asgn_b_rows = db_fetchall(
                    conn2,
                    f"""SELECT a.segment_id, c.name, a.confidence, a.rationale
                       FROM assignment a JOIN code c ON c.id = a.code_id
                       WHERE a.coding_run_id = ? AND a.segment_id IN ({placeholders})""",
                    (run_b["id"], *seg_ids),
                )
                conn2.close()

                seg_by_id = {row["id"]: row for row in seg_rows}
                for row in asgn_a_rows:
                    asgn_a_by_seg.setdefault(row["segment_id"], []).append(row)
                for row in asgn_b_rows:
                    asgn_b_by_seg.setdefault(row["segment_id"], []).append(row)

            for d in disagreements:
                seg_id = d.get("segment_id")
                seg = seg_by_id.get(seg_id)
                asgn_a = asgn_a_by_seg.get(seg_id, [])
                asgn_b = asgn_b_by_seg.get(seg_id, [])

                seg_txt = (seg["text"][:300] + "…") if seg and len(seg["text"]) > 300 else (seg["text"] if seg else "")
                codes_a_str = ", ".join(a["name"] for a in asgn_a) or "(none)"
                codes_b_str = ", ".join(b["name"] for b in asgn_b) or "(none)"

                with st.expander(f"Segment {seg_id}: A={codes_a_str} | B={codes_b_str}"):
                    st.markdown(f"> {seg_txt}")
                    st.divider()
                    col_a, col_b = st.columns(2)

                    with col_a:
                        st.markdown("**Coder A:**")
                        for a in asgn_a:
                            conf = f" *(conf: {a['confidence']:.2f})*" if a.get("confidence") else ""
                            st.markdown(f"- `{a['name']}`{conf}")
                            if a.get("rationale"):
                                st.caption(a["rationale"])

                    with col_b:
                        st.markdown("**Coder B:**")
                        for b in asgn_b:
                            conf = f" *(conf: {b['confidence']:.2f})*" if b.get("confidence") else ""
                            st.markdown(f"- `{b['name']}`{conf}")
                            if b.get("rationale"):
                                st.caption(b["rationale"])

                    with st.form(f"resolve_disagree_{seg_id}"):
                        resolution_note = st.text_area(
                            "Your decision",
                            placeholder="Briefly explain your final coding decision for this segment.",
                            key=f"disagree_note_{seg_id}",
                        )
                        save_note_btn = st.form_submit_button("Save Note")

                    if save_note_btn and resolution_note.strip():
                        add_memo(
                            db_path,
                            project_id,
                            title=f"Disagreement resolution — Segment {seg_id}",
                            content=resolution_note.strip(),
                        )
                        update_project_status(db_path, project_id, "discussing")
                        st.success("Decision saved as a memo.")

# ── Memos ─────────────────────────────────────────────────────────────────────
with tab_memos:
    st.markdown("### Analytical Memos")
    st.markdown(
        "Memos are notes you write to yourself during analysis — reflections on patterns, "
        "decisions, and emerging interpretations."
    )

    from polyphony_gui.db import get_memos
    memos = get_memos(db_path, project_id)

    with st.form("new_memo_form"):
        memo_title = st.text_input("Title", placeholder="e.g. 'Why I merged codes X and Y'")
        memo_body = st.text_area("Memo", placeholder="Write your analytical note here…", height=120)
        save_memo_btn = st.form_submit_button("Save Memo", type="primary")

    if save_memo_btn:
        if not memo_title.strip() or not memo_body.strip():
            st.error("Both title and content are required.")
        else:
            add_memo(db_path, project_id, memo_title.strip(), memo_body.strip())
            update_project_status(db_path, project_id, "discussing")
            st.success("Memo saved.")
            st.rerun()

    st.divider()

    if not memos:
        st.info("No memos yet.")
    else:
        for memo in memos:
            with st.expander(f"**{memo.get('title', 'Memo')}** — {(memo.get('created_at') or '')[:16]}"):
                st.write(memo.get("content", ""))
