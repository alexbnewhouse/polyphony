"""
Polyphony GUI — Projects
========================
Create and manage QDA projects.
"""

import json
import logging

import streamlit as st

from polyphony_gui.components import render_sidebar
from polyphony_gui.services import safe_error_message

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.db import (
    create_project,
    get_project_db,
    get_project_stats,
    list_projects,
    load_project,
)
from polyphony_gui.models import (
    ANTHROPIC_MODELS,
    OPENAI_MODELS,
    default_model,
    list_ollama_models,
    model_options_for_provider,
    ollama_is_running,
)

st.set_page_config(page_title="Projects — Polyphony", page_icon="📁", layout="wide")
render_sidebar()

# ─── Page ─────────────────────────────────────────────────────────────────────
st.title("📁 Projects")

tab_list, tab_new = st.tabs(["My Projects", "Create New Project"])

# ── Tab: list ─────────────────────────────────────────────────────────────────
with tab_list:
    projects = list_projects()
    if not projects:
        st.info("No projects found. Use the **Create New Project** tab to get started.")
    else:
        for p in projects:
            db = p["db_path"]
            stats = get_project_stats(db, p["id"])
            rqs = json.loads(p.get("research_questions") or "[]")

            STATUS_COLORS = {
                "setup": "🔵",
                "importing": "🟡",
                "inducing": "🟡",
                "calibrating": "🟠",
                "coding": "🟠",
                "irr": "🟠",
                "discussing": "🟠",
                "analyzing": "🟢",
                "done": "✅",
            }
            icon = STATUS_COLORS.get(p["status"], "⚪")

            with st.expander(
                f"{icon} **{p['name']}** — {p['methodology'].replace('_', ' ').title()}",
                expanded=False,
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Documents", stats["documents"])
                c2.metric("Segments", stats["segments"])
                c3.metric("Codes", stats["codes"])
                c4.metric("Status", p["status"].title())

                if rqs:
                    st.markdown("**Research Questions:**")
                    for i, q in enumerate(rqs, 1):
                        st.write(f"  {i}. {q}")

                if st.button("Set as Active Project", key=f"activate_{p['slug']}"):
                    st.session_state.active_project_slug = p["slug"]
                    st.session_state.active_project_db = db
                    st.session_state.active_project = load_project(db)
                    st.success(f"'{p['name']}' is now the active project.")
                    st.rerun()

# ── Tab: create ───────────────────────────────────────────────────────────────
with tab_new:
    st.markdown("### Create a New Project")
    st.markdown(
        "Fill in the details below. You can always add or edit research questions later. "
        "Need help choosing a model? See the **⚙️ Settings** page."
    )

    # ── Helper: render a model selector for one coder ─────────────────────────
    def _coder_model_section(label: str, key_prefix: str, default_provider: str = "ollama"):
        """Render provider + model controls for one coder. Returns (provider, model)."""
        st.markdown(f"**{label}**")

        PROVIDER_LABELS = {
            "ollama":    "🖥️  Ollama — local, free, private",
            "openai":    "☁️  OpenAI — GPT-4o (requires API key)",
            "anthropic": "☁️  Anthropic — Claude (requires API key)",
        }
        provider = st.selectbox(
            "Provider",
            options=list(PROVIDER_LABELS.keys()),
            format_func=lambda x: PROVIDER_LABELS[x],
            key=f"{key_prefix}_provider",
            help=(
                "**Ollama:** runs models locally on your machine — no API key, completely private.  \n"
                "**OpenAI / Anthropic:** cloud models — require an API key set in your environment."
            ),
        )

        # Live model options depend on provider
        if provider == "ollama":
            installed = list_ollama_models()
            if installed:
                # Selectbox with installed models + manual option
                options = installed + ["(enter manually)"]
                sel = st.selectbox(
                    "Installed model",
                    options=options,
                    key=f"{key_prefix}_installed_sel",
                    help="These are the models currently installed in your local Ollama instance.",
                )
                if sel == "(enter manually)":
                    model = st.text_input(
                        "Model name",
                        value=default_model("ollama"),
                        key=f"{key_prefix}_model_manual",
                        help="Enter the Ollama model name, e.g. `llama3.1:8b`. Pull with `ollama pull <name>`.",
                    )
                else:
                    model = sel
            else:
                if ollama_is_running():
                    st.warning(
                        "Ollama is running but no models are installed. "
                        "Pull one with `ollama pull llama3.1:8b`."
                    )
                else:
                    st.info(
                        "Ollama is not running. Start it with `ollama serve`, or choose a "
                        "cloud provider above. You can still type a model name manually."
                    )
                model = st.text_input(
                    "Model name",
                    value=default_model("ollama"),
                    key=f"{key_prefix}_model_manual",
                    help="Enter the Ollama model name, e.g. `llama3.1:8b`. Pull with `ollama pull <name>`.",
                )

        elif provider == "openai":
            catalog = OPENAI_MODELS
            ids = [m["id"] for m in catalog]
            labels = {m["id"]: m["label"] for m in catalog}
            ids_with_custom = ids + ["(enter manually)"]
            sel = st.selectbox(
                "Model",
                options=ids_with_custom,
                format_func=lambda x: labels.get(x, x),
                key=f"{key_prefix}_openai_sel",
                help="Select from the recommended OpenAI models, or enter a custom model ID.",
            )
            if sel == "(enter manually)":
                model = st.text_input(
                    "Custom model ID",
                    value="gpt-4o",
                    key=f"{key_prefix}_model_manual",
                )
            else:
                model = sel

        else:  # anthropic
            catalog = ANTHROPIC_MODELS
            ids = [m["id"] for m in catalog]
            labels = {m["id"]: m["label"] for m in catalog}
            ids_with_custom = ids + ["(enter manually)"]
            sel = st.selectbox(
                "Model",
                options=ids_with_custom,
                format_func=lambda x: labels.get(x, x),
                key=f"{key_prefix}_anthropic_sel",
                help="Select from the recommended Anthropic / Claude models, or enter a custom model ID.",
            )
            if sel == "(enter manually)":
                model = st.text_input(
                    "Custom model ID",
                    value="claude-sonnet-4-6",
                    key=f"{key_prefix}_model_manual",
                )
            else:
                model = sel

        return provider, model

    # ── Form ──────────────────────────────────────────────────────────────────
    # Model selectors must live outside the form because they trigger reruns
    # (selectbox onChange).  We collect values in session state, then read them
    # inside the form's submit handler.

    st.divider()
    st.markdown("#### Basic Information")

    name = st.text_input(
        "Project Name *",
        placeholder="e.g. Housing Precarity Study 2026",
        help="A short, descriptive name for your project.",
        key="new_proj_name",
    )
    description = st.text_area(
        "Description",
        placeholder="Brief summary of what this project is about.",
        height=80,
        key="new_proj_desc",
    )
    methodology = st.selectbox(
        "Methodology *",
        options=["grounded_theory", "thematic_analysis", "content_analysis"],
        format_func=lambda x: {
            "grounded_theory": "Grounded Theory — inductive, theory-building from data",
            "thematic_analysis": "Thematic Analysis — identify patterns and themes",
            "content_analysis": "Content Analysis — systematic, often deductive coding",
        }[x],
        help=(
            "**Grounded Theory:** codes emerge inductively from data. Best for exploratory research.  \n"
            "**Thematic Analysis:** identify recurring patterns and themes (Braun & Clarke approach).  \n"
            "**Content Analysis:** apply a pre-defined coding scheme systematically."
        ),
        key="new_proj_method",
    )

    st.markdown("**Research Questions** (add up to 5)")
    st.caption("These are passed to the AI coders to help them focus on relevant content.")
    rqs = []
    for i in range(1, 6):
        q = st.text_input(
            f"RQ {i}",
            key=f"new_proj_rq_{i}",
            placeholder=f"Research question {i} (optional)",
        )
        if q.strip():
            rqs.append(q.strip())

    st.divider()
    st.markdown("#### AI Coder Settings")
    st.markdown(
        "Polyphony uses **two independent AI coders**. They work in isolation and their "
        "outputs are compared to measure inter-rater reliability. You can use the same "
        "model with different seeds, or different models for diverse perspectives."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        provider_a, model_a = _coder_model_section("Coder A", "ca")
        seed_a = st.number_input(
            "Seed A",
            value=42,
            min_value=0,
            max_value=99999,
            key="new_proj_seed_a",
            help=(
                "Random seed for reproducibility. A fixed seed ensures the model's outputs "
                "are the same each time you run the same inputs. Use a different seed for "
                "Coder B so the two coders behave independently."
            ),
        )

    with col_b:
        provider_b, model_b = _coder_model_section("Coder B", "cb")
        seed_b = st.number_input(
            "Seed B",
            value=137,
            min_value=0,
            max_value=99999,
            key="new_proj_seed_b",
            help="Use a different seed than Coder A so the two coders produce independent outputs.",
        )

    temperature = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=0.1,
        step=0.05,
        key="new_proj_temp",
        help=(
            "Controls randomness in model outputs.  \n"
            "- `0.0` = fully deterministic, maximally reproducible  \n"
            "- `0.1–0.2` = slight variation, still consistent (recommended for coding)  \n"
            "- `0.7+` = more creative, less predictable  \n\n"
            "For qualitative coding, keep temperature at **0.0–0.2**."
        ),
    )

    # Vision capability warnings
    VISION_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "claude-opus-4-6",
                     "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                     "claude-opus-4-5-20250514", "claude-sonnet-4-5-20250514"}
    VISION_OLLAMA = {"llava", "llava-phi3", "bakllava", "moondream"}
    def _has_vision(provider: str, model: str) -> bool:
        if provider in ("openai", "anthropic"):
            return model in VISION_MODELS
        return any(v in model.lower() for v in VISION_OLLAMA)

    if not _has_vision(provider_a, model_a) or not _has_vision(provider_b, model_b):
        non_vision = []
        if not _has_vision(provider_a, model_a):
            non_vision.append(f"Coder A ({model_a})")
        if not _has_vision(provider_b, model_b):
            non_vision.append(f"Coder B ({model_b})")
        st.info(
            f"⚠️ {', '.join(non_vision)} may not support image analysis. "
            "If you plan to code images, consider using GPT-4o, Claude, or LLaVA."
        )

    st.divider()
    if st.button("Create Project", type="primary", key="create_project_btn"):
        if not name.strip():
            st.error("Please enter a project name.")
        elif not model_a.strip():
            st.error("Please enter a model name for Coder A.")
        elif not model_b.strip():
            st.error("Please enter a model name for Coder B.")
        else:
            try:
                p = create_project(
                    name=name.strip(),
                    description=description.strip(),
                    methodology=methodology,
                    research_questions=rqs,
                    model_a=model_a.strip(),
                    model_b=model_b.strip(),
                    provider_a=provider_a,
                    provider_b=provider_b,
                    seed_a=int(seed_a),
                    seed_b=int(seed_b),
                    temperature=float(temperature),
                )
                db = str(get_project_db(p["slug"]))
                st.session_state.active_project_slug = p["slug"]
                st.session_state.active_project_db = db
                st.session_state.active_project = p
                if not rqs:
                    st.warning(
                        "No research questions entered. You can add them later from the project settings."
                    )
                st.success(
                    f"Project **{p['name']}** created! Navigate to **Import Data** to add documents."
                )
                st.rerun()
            except ValueError as e:
                st.error(safe_error_message(e, "Project creation"))
