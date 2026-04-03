"""
Polyphony GUI — Settings
=========================
Environment configuration, provider connection tests, and a reference guide
for every setting available in Polyphony.
"""

import logging
import os

import streamlit as st

from polyphony_gui.components import render_sidebar

logger = logging.getLogger("polyphony_gui")
from polyphony_gui.models import (
    ANTHROPIC_MODELS,
    OPENAI_MODELS,
    check_api_keys,
    get_ollama_host,
    list_ollama_models,
    ollama_is_running,
)

st.set_page_config(page_title="Settings — Polyphony", page_icon="⚙️", layout="wide")
render_sidebar()

st.title("⚙️ Settings & Model Reference")
st.markdown(
    "This page explains every configuration option and lets you verify that "
    "your AI providers are reachable before starting a project."
)

# ─────────────────────────────────────────────────────────────────────────────
# Tab layout
# ─────────────────────────────────────────────────────────────────────────────
tab_providers, tab_models, tab_options, tab_env = st.tabs([
    "Provider Status",
    "Available Models",
    "Settings Reference",
    "Environment Variables",
])

# ── Provider Status ───────────────────────────────────────────────────────────
with tab_providers:
    st.markdown("### Provider Connection Status")
    st.markdown(
        "Polyphony supports three AI providers. Check that the ones you intend "
        "to use are properly configured before creating a project."
    )

    col_ollama, col_openai, col_anthropic = st.columns(3)

    # --- Ollama ---
    with col_ollama:
        with st.container(border=True):
            st.markdown("#### 🖥️ Ollama (Local)")
            st.caption("Run open-source models privately on your own machine — no API key required.")
            host = get_ollama_host()
            st.code(host, language=None)
            if st.button("Test connection", key="test_ollama"):
                with st.spinner("Connecting…"):
                    if ollama_is_running():
                        models = list_ollama_models()
                        st.success(f"Connected — {len(models)} model(s) installed")
                    else:
                        st.error(
                            "Cannot reach Ollama. Make sure it is running:\n\n"
                            "```\nollama serve\n```\n\n"
                            "Or set `POLYPHONY_OLLAMA_HOST` to your Ollama address."
                        )
            st.markdown(
                "**Install Ollama:** https://ollama.com/download  \n"
                "**Pull a model:** `ollama pull llama3.1:8b`"
            )

    # --- OpenAI ---
    with col_openai:
        with st.container(border=True):
            st.markdown("#### ☁️ OpenAI")
            st.caption("Use GPT-4o and other OpenAI models via the cloud.")
            keys = check_api_keys()
            if keys["openai"]:
                st.success(f"API key set: `{keys['openai']}`")
            else:
                st.warning("No API key found.")
                st.markdown(
                    "Set the environment variable:\n\n"
                    "```bash\nexport OPENAI_API_KEY='sk-...'\n```"
                )
            st.markdown("**Get a key:** https://platform.openai.com/api-keys")

    # --- Anthropic ---
    with col_anthropic:
        with st.container(border=True):
            st.markdown("#### ☁️ Anthropic")
            st.caption("Use Claude models via the Anthropic API.")
            if keys["anthropic"]:
                st.success(f"API key set: `{keys['anthropic']}`")
            else:
                st.warning("No API key found.")
                st.markdown(
                    "Set the environment variable:\n\n"
                    "```bash\nexport ANTHROPIC_API_KEY='sk-ant-...'\n```"
                )
            st.markdown("**Get a key:** https://console.anthropic.com/settings/keys")


# ── Available Models ──────────────────────────────────────────────────────────
with tab_models:
    st.markdown("### Available Models")

    # -- Ollama --
    st.markdown("#### 🖥️ Ollama — Installed Models")
    ollama_running = ollama_is_running(timeout=2.0)

    if not ollama_running:
        st.info(
            "Ollama is not running. Start it with `ollama serve` to see your installed models."
        )
    else:
        installed = list_ollama_models()
        if installed:
            st.success(f"{len(installed)} model(s) installed on this machine:")
            cols = st.columns(3)
            for i, name in enumerate(sorted(installed)):
                cols[i % 3].code(name, language=None)
        else:
            st.warning(
                "Ollama is running but no models are installed yet.\n\n"
                "Pull a model from the terminal:\n```\nollama pull llama3.1:8b\n```"
            )

    st.markdown("**Recommended Ollama models for QDA:**")
    recommended = {
        "llama3.1:8b":    "Good baseline — fast, fits on most machines (8 GB RAM)",
        "llama3.1:70b":   "High quality — requires ~40 GB RAM or a good GPU",
        "mistral:7b":     "Lightweight alternative to Llama",
        "gemma2:9b":      "Google's Gemma 2 — strong reasoning",
        "qwen2.5:14b":    "Alibaba's Qwen 2.5 — excellent instruction following",
        "phi4:14b":       "Microsoft Phi-4 — strong reasoning in small footprint",
    }
    import pandas as pd
    st.dataframe(
        pd.DataFrame([{"Model": k, "Notes": v} for k, v in recommended.items()]),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Pull any model with: `ollama pull <model-name>`")

    st.divider()

    # -- OpenAI --
    st.markdown("#### ☁️ OpenAI Models")
    st.dataframe(
        pd.DataFrame([{"Model ID": m["id"], "Description": m["label"]} for m in OPENAI_MODELS]),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Pricing and availability: https://platform.openai.com/docs/models  \n"
        "Requires `OPENAI_API_KEY` environment variable."
    )

    st.divider()

    # -- Anthropic --
    st.markdown("#### ☁️ Anthropic / Claude Models")
    st.dataframe(
        pd.DataFrame([{"Model ID": m["id"], "Description": m["label"]} for m in ANTHROPIC_MODELS]),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Pricing and availability: https://docs.anthropic.com/en/docs/models-overview  \n"
        "Requires `ANTHROPIC_API_KEY` environment variable."
    )

    st.divider()
    st.info(
        "**Mixing providers:** Coder A and Coder B can use different providers and models. "
        "Using two distinct models can increase diversity of perspectives in your analysis."
    )


# ── Settings Reference ────────────────────────────────────────────────────────
with tab_options:
    st.markdown("### Settings Reference")
    st.markdown(
        "A complete explanation of every option available when creating or configuring "
        "a Polyphony project."
    )

    # Project settings
    with st.expander("📁 Project Settings", expanded=True):
        st.markdown("""
| Setting | Description |
|---------|-------------|
| **Project Name** | A short descriptive name for your study. Used to create the project folder and database. |
| **Description** | Free-text summary of the project. Optional but recommended for documentation. |
| **Methodology** | The qualitative approach that guides how codes are developed and applied (see below). |
| **Research Questions** | Up to 5 research questions. These are passed to the AI coders to help them prioritize relevant content. |

**Methodology options:**

- **Grounded Theory** — Codes emerge inductively from the data. The AI will suggest codes freely without a pre-existing framework. Best for exploratory research where you do not yet have a theoretical model.
- **Thematic Analysis** — Identify recurring patterns and themes across the dataset. Follows Braun & Clarke's reflexive thematic analysis approach.
- **Content Analysis** — Systematic, often deductive coding against a pre-defined codebook. Best when you have an existing coding scheme you want to apply rigorously.
""")

    # Coder settings
    with st.expander("🤖 AI Coder Settings"):
        st.markdown("""
Polyphony uses **two independent AI coders** — Coder A and Coder B — who work in isolation
and never see each other's output until the inter-rater reliability (IRR) step. This mirrors
the methodological standard of using two independent human coders.

| Setting | Description |
|---------|-------------|
| **Provider** | Which AI service provides the model (Ollama, OpenAI, or Anthropic). |
| **Model** | The specific language model within that provider. See the **Available Models** tab for options. |
| **Seed** | An integer that initialises the random number generator. Using a fixed seed makes the model's outputs reproducible across runs. Coder A and B should use *different* seeds so their outputs are independent. |
| **Temperature** | Controls randomness in model outputs. `0.0` = fully deterministic; `1.0` = very creative. **Recommended: 0.0–0.2 for coding tasks** to maximise consistency and reproducibility. |

**Choosing a model:**

| Goal | Recommendation |
|------|----------------|
| Free, private, no internet | Ollama with `llama3.1:8b` or larger |
| Best quality, budget flexible | Anthropic `claude-sonnet-4-6` or OpenAI `gpt-4o` |
| Fast iteration / large corpus | OpenAI `gpt-4o-mini` or Anthropic `claude-haiku-4-5-20251001` |
| Diverse coder perspectives | Use different providers for Coder A and B |

**Why two different seeds?**
The seed determines the model's sampling sequence. Two coders with the same seed would produce
near-identical outputs, defeating the purpose of independent coding. The defaults (42 and 137)
are arbitrary — any two distinct values work.
""")

    # Import settings
    with st.expander("📄 Import & Segmentation Settings"):
        st.markdown("""
| Setting | Description |
|---------|-------------|
| **Segmentation strategy** | How documents are divided into codeable units (segments). |
| **CSV content column** | For CSV files: the column name that contains the text to be coded. |
| **Fixed window size** | When using *fixed* segmentation: how many words per segment. |

**Segmentation strategies:**

| Strategy | How it works | Best for |
|----------|--------------|----------|
| **Paragraph** | Split on blank lines | Interview transcripts, essays, news articles |
| **Sentence** | Split on sentence boundaries (`.`, `?`, `!`) | Survey open-ends, short responses |
| **Manual** | Each uploaded file becomes one segment | Short documents (< 500 words), images |
| **Fixed** | Split into chunks of N words with optional overlap | Long unstructured text, social media |

**Tip:** For most qualitative research, *paragraph* segmentation is the best default.
Segments that are too short (< 30 words) may lack context; segments that are too long
(> 500 words) may contain multiple themes that a single code cannot capture.
""")

    # Calibration settings
    with st.expander("⚖️ Calibration Settings"):
        st.markdown("""
| Setting | Description |
|---------|-------------|
| **Number of calibration segments** | How many segments both coders will code during calibration. More = more reliable estimate, but slower. 10–20 is typical. |
| **Acceptable agreement threshold** | The minimum Krippendorff's α required to consider calibration successful. |
| **Re-select calibration segments** | If checked, a new random sample of segments is drawn. If unchecked, the previously selected set is reused (consistent across calibration rounds). |

**Inter-rater reliability thresholds:**

| α value | Interpretation |
|---------|---------------|
| ≥ 0.80 | Acceptable for published research |
| 0.67–0.79 | Moderate — consider refining ambiguous codes |
| < 0.67 | Low — codebook likely needs revision |

Krippendorff (2004) recommends α ≥ 0.80 for data used in decision-making.
""")

    # Coding settings
    with st.expander("🤖 Coding Settings"):
        st.markdown("""
| Setting | Description |
|---------|-------------|
| **Coder(s) to run** | Choose to run both coders, or just one. Running both is required for IRR computation. |
| **Coding approach** | Whether coders apply only existing codes or can suggest new ones. |
| **Resume** | Skip segments already coded in a previous run (useful if a run was interrupted). |

**Coding approaches:**

| Approach | Behaviour |
|----------|-----------|
| **Open coding** | Coders apply existing codebook codes *and* may flag potential new codes. Suitable early in analysis. |
| **Deductive coding** | Coders strictly apply only the existing codebook. Suitable after codebook is finalised. |
""")

    # Analysis settings
    with st.expander("🔍 Analysis Settings"):
        st.markdown("""
| Setting | Description |
|---------|-------------|
| **Theme synthesis focus** | Optional text to guide the AI theme synthesizer (e.g., a specific research question or theoretical angle). |
| **Number of themes** | How many high-level themes to synthesize from your codes. 3–7 is typical for most studies. |
""")


# ── Environment Variables ─────────────────────────────────────────────────────
with tab_env:
    st.markdown("### Environment Variables")
    st.markdown(
        "Polyphony reads configuration from environment variables. "
        "Set them in your shell profile (`~/.zshrc`, `~/.bashrc`) or pass them "
        "on the command line when launching."
    )

    env_vars = {
        "POLYPHONY_OLLAMA_HOST": {
            "default": "http://localhost:11434",
            "description": "Base URL of your Ollama instance. Override this if Ollama runs on a remote host or a non-default port.",
            "example": "http://192.168.1.10:11434",
        },
        "OPENAI_API_KEY": {
            "default": "(none)",
            "description": "Your OpenAI API key. Required to use GPT-4o or other OpenAI models.",
            "example": "sk-proj-...",
        },
        "ANTHROPIC_API_KEY": {
            "default": "(none)",
            "description": "Your Anthropic API key. Required to use Claude models.",
            "example": "sk-ant-api03-...",
        },
        "POLYPHONY_PROJECTS_DIR": {
            "default": "~/.polyphony/projects/",
            "description": "Directory where all project databases and files are stored. Override to keep projects on an external drive or shared network folder.",
            "example": "/Volumes/Research/polyphony-projects",
        },
    }

    for var, info in env_vars.items():
        current_val = os.environ.get(var)
        is_set = current_val is not None

        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**`{var}`**")
                st.caption(info["description"])
                st.markdown(f"Default: `{info['default']}`  |  Example: `{info['example']}`")
            with c2:
                if is_set:
                    masked = (current_val[:4] + "…" + current_val[-4:]) if len(current_val) > 8 else "****"
                    st.success(f"Set: `{masked}`")
                else:
                    st.warning("Not set")

    st.divider()
    st.markdown("**Quick setup example** (add to `~/.zshrc` or `~/.bashrc`):")
    st.code(
        """# Polyphony AI providers
export OPENAI_API_KEY="sk-proj-..."
export ANTHROPIC_API_KEY="sk-ant-api03-..."

# Optional: custom projects directory
export POLYPHONY_PROJECTS_DIR="$HOME/research/polyphony"

# Optional: remote Ollama instance
export POLYPHONY_OLLAMA_HOST="http://localhost:11434"
""",
        language="bash",
    )
    st.markdown("After editing your shell profile, restart the terminal (or run `source ~/.zshrc`) and relaunch Polyphony.")
