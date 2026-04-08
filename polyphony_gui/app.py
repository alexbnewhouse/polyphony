"""
Polyphony GUI — Home
====================
Welcome screen, project selector, and workflow overview.
"""

import logging
from pathlib import Path

import streamlit as st

logger = logging.getLogger("polyphony_gui")


# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polyphony — Qualitative Analysis",
    page_icon="🎼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Session state defaults ───────────────────────────────────────────────────
if "active_project_slug" not in st.session_state:
    st.session_state.active_project_slug = None
if "active_project_db" not in st.session_state:
    st.session_state.active_project_db = None
if "active_project" not in st.session_state:
    st.session_state.active_project = None

from polyphony_gui.components import render_sidebar
render_sidebar()

# ─── Main content ─────────────────────────────────────────────────────────────
st.title("🎼 Polyphony")
st.subheader("AI-Assisted Qualitative Data Analysis")

st.markdown("""
Polyphony lets you conduct rigorous qualitative research using AI language models as independent
coders — replicating the methodological standards of multi-researcher studies, without needing a
full team.
""")

# Workflow overview cards
st.markdown("### How it works")

STEPS = [
    ("📁", "1. Create Project", "Set your research questions, methodology, and AI model preferences."),
    ("📄", "2. Import Data", "Upload interview transcripts, survey responses, documents, or images."),
    ("🏷️", "3. Build Codebook", "Let the AI suggest codes from your data, then review and refine them."),
    ("⚖️", "4. Calibrate", "Run a calibration round so both AI coders agree on how to apply codes."),
    ("🤖", "5. Code Data", "Both AI coders independently code every segment of your corpus."),
    ("📊", "6. Check Agreement", "Measure inter-rater reliability (Krippendorff's α, Cohen's κ)."),
    ("💬", "7. Resolve Disagreements", "Review cases where coders disagreed and make final decisions."),
    ("🔍", "8. Analyze", "Explore code frequencies, saturation, and emerging themes."),
    ("📦", "9. Export", "Download your codebook, coded data, and a full replication package."),
]

cols = st.columns(3)
for i, (icon, title, desc) in enumerate(STEPS):
    with cols[i % 3]:
        with st.container(border=True):
            st.markdown(f"**{icon} {title}**")
            st.caption(desc)

st.divider()

# Active project summary
if st.session_state.active_project:
    p = st.session_state.active_project
    import json

    rqs = json.loads(p.get("research_questions") or "[]")

    st.markdown(f"### Current Project: {p['name']}")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Methodology", p["methodology"].replace("_", " ").title())
    with col2:
        STATUS_LABELS = {
            "setup": "⚙️ Setup",
            "importing": "📄 Importing Data",
            "inducing": "🏷️ Building Codebook",
            "calibrating": "⚖️ Calibrating",
            "coding": "🤖 Coding",
            "irr": "📊 Checking Agreement",
            "discussing": "💬 Resolving",
            "analyzing": "🔍 Analyzing",
            "done": "✅ Complete",
        }
        label = STATUS_LABELS.get(p["status"], p["status"])
        st.metric("Status", label)
    with col3:
        db_path = st.session_state.active_project_db
        if db_path:
            from polyphony_gui.db import get_project_stats
            stats = get_project_stats(db_path, p["id"])
            st.metric("Documents", stats["documents"])

    if rqs:
        with st.expander("Research Questions"):
            for i, q in enumerate(rqs, 1):
                st.write(f"**RQ{i}:** {q}")

    st.markdown("Use the **sidebar** to navigate to each step of the workflow.")
else:
    st.info("👈 Select or create a project using the **Projects** page to get started.")

    # Practice mode
    st.divider()
    st.markdown("### 🧪 Try a Practice Project")
    st.markdown(
        "New to qualitative coding with AI? Start a practice project with sample data "
        "to learn the workflow before working with your own research data."
    )

    if st.button("Create Practice Project", key="practice_mode"):
        from polyphony_gui.db import create_project, get_project_db, load_project

        try:
            practice = create_project(
                name="Practice Project — Housing Study",
                description=(
                    "A sample project with fictional interview excerpts about housing insecurity. "
                    "Use this to familiarize yourself with Polyphony's workflow."
                ),
                methodology="thematic_analysis",
                research_questions=[
                    "How do participants describe their experiences with housing insecurity?",
                    "What coping strategies do participants report?",
                ],
                model_a="gpt-4o-mini",
                model_b="gpt-4o-mini",
                provider_a="openai",
                provider_b="openai",
                seed_a=42,
                seed_b=137,
                temperature=0.1,
            )
            db = str(get_project_db(practice["slug"]))

            # Pre-load sample data
            SAMPLE_SEGMENTS = [
                "I've been moving from place to place for the last two years. Each time the rent goes up, I have to find somewhere new. It's exhausting and it makes it hard to feel like I belong anywhere.",
                "The food bank has been a lifeline. Without it, I don't know how we'd eat some weeks. But there's a stigma — I never thought I'd be someone who needs that kind of help.",
                "My kids are in three different schools now because we keep moving. Their grades are dropping and I feel terrible about it, but I can't afford to stay in one place.",
                "I work two jobs, almost 60 hours a week, and I still can't save enough for a security deposit. The system feels rigged against people like me.",
                "Having a community garden plot has been one of the few stable things in my life. Even when everything else is chaos, I can go there and feel grounded.",
            ]

            from polyphony.db.connection import connect
            from pathlib import Path
            import tempfile

            conn = connect(Path(db))
            with tempfile.TemporaryDirectory() as tmpdir:
                sample_path = Path(tmpdir) / "sample_interviews.txt"
                sample_path.write_text("\n\n".join(SAMPLE_SEGMENTS), encoding="utf-8")

                from polyphony.io.importers import import_documents
                import_documents(
                    conn=conn,
                    project_id=practice["id"],
                    paths=[sample_path],
                    segment_strategy="paragraph",
                    project_dir=Path(db).parent,
                )
                conn.commit()
            conn.close()

            st.session_state.active_project_slug = practice["slug"]
            st.session_state.active_project_db = db
            st.session_state.active_project = practice
            st.success(
                "Practice project created with sample data! "
                "Navigate to **Codebook** to start building codes."
            )
            st.rerun()
        except Exception as e:
            from polyphony_gui.services import safe_error_message
            st.error(safe_error_message(e, "Practice project"))

st.divider()
st.caption(
    "Polyphony is open-source software for qualitative data analysis. "
    "All data is stored locally on your machine."
)
