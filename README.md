# polyphony — Collaborative Qualitative Data Analysis

**polyphony** is a command-line tool for conducting rigorous qualitative data analysis (QDA) with two AI language models working alongside you as independent coders. You can also participate as a full third coder for 3-way inter-rater reliability, lead codebook induction yourself, or code a representative sample while the LLMs code the full corpus. It supports both inductive and deductive coding, text and image data, and multiple model providers (Ollama, OpenAI, Anthropic). It is designed for solo social scientists who want the analytical benefits of multi-coder studies without a full research team.

---

## Overview

In a traditional multi-coder QDA study, two or more researchers independently code the same data, then discuss disagreements until they reach agreement. polyphony replicates this workflow:

- **You** act as lead researcher and supervisor
- **Coder A** and **Coder B** are two AI models (local via [Ollama](https://ollama.ai), or cloud via OpenAI/Anthropic)
- The system guides you through every stage: codebook design → calibration → coding → reliability → discussion → analysis → export

All model calls are logged with full prompts, responses, model versions, temperature, and seed — so every analytical decision is fully reproducible.

---

## Key Features

- **Inductive codebook design**: Both AIs suggest codes from a sample; you review and approve. With `--human-leads`, you propose codes first.
- **Deductive codebook import**: Import a pre-existing codebook from YAML, JSON, or CSV for theory-driven coding (`codebook import`)
- **Human-as-lead-coder**: Optionally code as a full third coder alongside the two LLMs for 3-way IRR, reducing correlated LLM bias
- **Calibration**: Structured rounds to align coders before full analysis, with optional 3-way calibration (`--include-supervisor`)
- **Independent coding**: Agents code without seeing each other's work. The supervisor can code all segments or a representative sample (`--sample-size`)
- **Deductive coding mode**: Strict codebook adherence with `--deductive` for theory-driven research
- **Inter-rater reliability**: Krippendorff's alpha (2-way and 3-way), pairwise Cohen's kappa, percent agreement
- **Flag & discussion system**: Ambiguous cases surface for structured debate
- **Analytical memos**: Write theoretical/methodological notes throughout
- **Multimodal image support**: Import and code images (PNG, JPEG, GIF, WebP, BMP, TIFF) alongside text using vision-capable models
- **Multiple model providers**: Ollama (local), OpenAI, Anthropic — mix and match across coders
- **Full replication package**: Every prompt, response, decision, and prompt hash is exportable
- **Supports multiple methodologies**: Grounded theory, thematic analysis, content analysis

---

## Quick Start

### 1. Install

```bash
# Install polyphony
pip install polyphony

# For local models: install Ollama (https://ollama.ai) and pull a model
ollama pull llama3.1:8b

# For cloud APIs (optional):
pip install polyphony[openai]      # OpenAI / Azure OpenAI
pip install polyphony[anthropic]   # Anthropic (Claude)
pip install polyphony[all-providers]  # Both
```

### 2. Create a project

```bash
mkdir my_study && cd my_study
polyphony project new --name "Housing Precarity Study 2026" --methodology grounded_theory
```

You will be prompted for your research questions. Then configure your two AI coders (default: same model, different random seeds for independent coding).

To choose the LLMs used for the two coders when creating a project, pass the `--model-a` and
`--model-b` flags to `polyphony project new`. Example:

```bash
# Local models (Ollama, default)
polyphony project new --name "My Study" --model-a llama3.1:8b --model-b llama3.2:3b

# Cloud models
polyphony project new --name "My Study" \
  --provider-a openai --model-a gpt-4o \
  --provider-b anthropic --model-b claude-sonnet-4-5-20250514

# Mix local and cloud for greater independence
polyphony project new --name "My Study" \
  --provider-a ollama --model-a llama3.1:8b \
  --provider-b openai --model-b gpt-4o
```

The default is `llama3.1:8b` via Ollama for both coders.

### 3. Import your data

```bash
# Plain text files (e.g. interview transcripts)
polyphony data import transcripts/*.txt

# CSV with a 'response' column
polyphony data import survey.csv --content-col response

# Word documents
polyphony data import interviews/*.docx

# JSON array
polyphony data import data.json

# Images (requires a vision-capable model for coding)
polyphony data import photos/*.jpg

# Image URLs from CSV
polyphony data fetch-images image_urls.csv --url-column url
```

### 4. Build or import a codebook

**Inductive** (generate codes from data):

```bash
polyphony codebook induce --sample-size 20          # AIs propose codes from a sample
polyphony codebook induce --human-leads              # you propose codes first
```

**Deductive** (import a pre-existing codebook):

```bash
polyphony codebook import my_framework.yaml          # YAML, JSON, or CSV
polyphony codebook import --finalize theory_codes.csv # import and finalize in one step
```

### 5. Calibrate your coders

```bash
polyphony calibrate run
```

Both AIs code a small calibration set. Disagreements are reviewed with agent explanations, and you refine code definitions until reliability is acceptable (Krippendorff's α ≥ 0.80 by default).

For 3-way calibration (you code alongside the AIs):

```bash
polyphony calibrate run --include-supervisor
```

### 6. Run independent coding

```bash
polyphony code run                                 # inductive (default)
polyphony code run --deductive                     # deductive (strict codebook adherence)
```

Both agents code the full corpus independently. Neither sees the other's work.

To code as a third coder yourself:

```bash
polyphony code run --agent all
polyphony code run --agent all --sample-size 50
```

### 7. Compute reliability

```bash
polyphony irr compute
polyphony irr compute --three-way
```

### 8. Discuss disagreements

```bash
polyphony discuss flags
polyphony discuss resolve <flag_id>
```

### 9. Analyse

```bash
polyphony analyze frequencies    # Which codes appear most?
polyphony analyze saturation     # Has coding reached saturation?
polyphony analyze themes         # AI-assisted theme synthesis
polyphony analyze co-occurrence  # Which codes appear together?
```

### 10. Export

```bash
polyphony export replication   # Full replication package
polyphony export codebook      # Codebook as YAML/CSV
polyphony export assignments   # All coding decisions as CSV
```

### Practice Workflow (Offline by default)

Use practice mode to train on a sandbox project before running real studies.

```bash
# See available offline domains
polyphony practice --list-domains

# Create an offline synthetic sandbox (default mode)
polyphony practice --domain housing --segments 20

# Practice with your own local files in a sandbox
polyphony practice --source-file transcripts/interview_01.txt --source-file transcripts/interview_02.txt

# Optional: generate synthetic practice data via Ollama
polyphony practice --topic "climate anxiety among graduate students" --segments 25
```

Practice mode never auto-runs coding commands for you. It creates a sandbox project,
imports training data, and then prints the recommended next commands so you stay in control.

---

## Deductive Coding

For theory-driven research where the codebook is established before data collection, polyphony supports a deductive workflow:

1. **Import your codebook** from YAML, JSON, or CSV:

```bash
polyphony codebook import populism_framework.yaml --finalize
```

The import format matches what `polyphony export codebook` produces:

```yaml
codes:
  - name: POPULIST_RHETORIC
    level: open
    description: Speaker uses populist framing (us vs them, anti-elite)
    inclusion_criteria: "Anti-elite language, people vs establishment"
    exclusion_criteria: "Policy disagreement without populist framing"
```

2. **Run coding in deductive mode**:

```bash
polyphony code run --deductive
```

In deductive mode, the AI coders are instructed to apply the codebook strictly — they will not suggest new codes or flag missing categories. This is appropriate when your codebook represents a theoretical framework rather than an emergent coding scheme.

---

## Cloud API Models

polyphony supports OpenAI-compatible APIs and Anthropic alongside local Ollama models. This is useful for:

- **Larger, more capable models** (GPT-4o, Claude) that may code more accurately
- **Faster processing** of large corpora via cloud inference
- **Cross-provider independence** — using different providers for Coder A and Coder B tests whether results depend on a specific model architecture

```bash
# Set API keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."

# Create project with cloud models
polyphony project new --name "My Study" \
  --provider-a openai --model-a gpt-4o \
  --provider-b anthropic --model-b claude-sonnet-4-5-20250514
```

**Privacy note:** When using cloud APIs, your data is sent to external servers. Ensure you have appropriate data-sharing agreements and IRB approval. For sensitive data, use Ollama (which runs entirely locally).

---

## Project Directory Structure

Each project is stored in `~/.polyphony/projects/<slug>/`:

```
~/.polyphony/projects/housing-precarity-2026/
└── project.db          # Single SQLite file containing everything
```

A `.polyphony_project` marker file in your working directory points to the active project.

For safety, marker targets must resolve inside `POLYPHONY_PROJECTS_DIR`.
If a marker points outside that root, polyphony refuses to use it and asks you to reopen a valid project.

---

## Full Command Reference

```
polyphony project new          Create a new project
polyphony project open         Set active project
polyphony project list         List all projects
polyphony project status       Show pipeline status and counts

polyphony practice             Create an offline-first practice sandbox

polyphony data import          Import documents (txt, csv, json, docx)
polyphony data fetch-images    Fetch image URLs from CSV and import
polyphony data list            List imported documents
polyphony data show            Display a document or its segments

polyphony codebook induce      AI-assisted codebook induction (--human-leads)
polyphony codebook import      Import codebook from YAML/JSON/CSV (--finalize)
polyphony codebook show        Display codebook as a tree
polyphony codebook add         Add a code manually
polyphony codebook edit        Edit a code in $EDITOR
polyphony codebook finalize    Mark codebook as final
polyphony codebook history     Show all codebook versions

polyphony calibrate run        Run calibration round(s) (--include-supervisor)

polyphony code run             Run independent coding (--agent all, --sample-size, --deductive)
polyphony code status          Show coding progress
polyphony code show            Show codes for a specific segment

polyphony irr compute          Calculate inter-rater reliability (--three-way)
polyphony irr show             Display IRR results
polyphony irr disagreements    List coding disagreements

polyphony discuss flags        List open flags
polyphony discuss resolve      Resolve a flag (with agent discussion)
polyphony discuss raise        Raise a flag on a segment
polyphony discuss summary      Flag resolution summary

polyphony memo new             Write an analytical memo
polyphony memo list            List all memos
polyphony memo show            Display a memo

polyphony analyze frequencies  Code frequency table
polyphony analyze saturation   Theoretical saturation check
polyphony analyze themes       AI-assisted theme synthesis
polyphony analyze co-occurrence Code co-occurrence matrix

polyphony export codebook      Export codebook (yaml/json/csv)
polyphony export assignments   Export assignments (csv/json)
polyphony export memos         Export memos (md/json)
polyphony export llm-log       Export full LLM audit log (jsonl)
polyphony export replication   Generate full replication package
```

---

## Returning to an Existing Project

If you close your terminal and want to continue working on a project:

```bash
# See all your projects and their slugs
polyphony project list

# Re-activate a project in your working directory
polyphony project open housing-precarity-2026

# Or pass the slug explicitly to any command
polyphony --project housing-precarity-2026 project status
```

The `.polyphony_project` file in your working directory remembers which project is active; `project open` updates it.

---

## Requirements

- Python 3.10+
- At least one model provider:
  - **Ollama** (default): [ollama.ai](https://ollama.ai) running locally, ~4 GB RAM per model
  - **OpenAI**: API key in `OPENAI_API_KEY` environment variable
  - **Anthropic**: API key in `ANTHROPIC_API_KEY` environment variable

If you only use offline practice generation (`polyphony practice` without `--topic`) and file import/export,
you can start without Ollama.

### Python dependencies

```
click, rich, pydantic, ollama, krippendorff, scikit-learn, numpy, pandas, PyYAML, python-docx
```

**Optional**:

```bash
pip install polyphony[images]          # Pillow for image metadata
pip install polyphony[openai]          # OpenAI API support
pip install polyphony[anthropic]       # Anthropic API support
pip install polyphony[all-providers]   # All cloud providers
```

---

## Ollama Troubleshooting

**`Ollama call failed ... Is Ollama running?`**
Start the Ollama server: `ollama serve`

**`Model 'llama3.1:8b' not found in Ollama`**
Pull the model first: `ollama pull llama3.1:8b`

**Slow responses / timeouts**
Try a smaller/faster model, e.g. `llama3.2:3b`, or a quantized variant (`llama3.1:8b-q4_0`).

**Inconsistent outputs despite seed=0**
Ollama's seed support varies by model. Some models (e.g. Mistral) are more deterministic than others. For maximum reproducibility, set `--temperature 0.0` when creating the project.

**Image coding fails or returns generic descriptions**
Use a vision-capable model for coding image segments (for example, `llava`).

**Check Ollama logs**
```bash
ollama serve 2>&1 | tee ollama.log
```

---

## Supported Methodologies

| Methodology | Use when... |
|---|---|
| `grounded_theory` | Building theory from data; open/axial/selective coding |
| `thematic_analysis` | Identifying patterns across the dataset |
| `content_analysis` | Systematic, replicable description of text content |

---

## Replicability

Every AI coding decision is logged with:
- **Model name and digest** (exact model weights via Ollama, or model ID for cloud APIs)
- **Seed and temperature** (for deterministic reproduction where supported)
- **Full system and user prompts** (including the complete codebook version)
- **Prompt hash** (SHA-256 of combined prompts for prompt sensitivity tracking)
- **Full response text** and parsed output
- **Timestamps and duration**

The `polyphony export replication` command packages all of this into a self-contained directory with scripts to verify checksums and re-run individual calls.

---

## Quality and Testing

polyphony's test suite is designed to avoid confirmation bias by combining:

- Unit tests for deterministic helpers (segmentation, parsing, DB helpers)
- Integration tests for end-to-end workflows (imports, coding, IRR, export)
- Adversarial tests that assert failure paths and guardrails (for example: unsafe redirects, invalid marker paths, non-overlapping IRR inputs, incompatible CLI options)
- Scenario-based orchestration tests for calibration and coding session control flow (resume behavior, superseding incomplete runs, threshold-driven calibration exits, and 3-way calibration paths)

Run the full suite:

```bash
pytest -q
```

Run targeted orchestration tests:

```bash
pytest tests/test_coding_pipeline.py tests/test_calibration_pipeline.py -q
```

Run with coverage and missing-lines report:

```bash
pytest --cov=polyphony --cov-report=term-missing:skip-covered -q
```

---

## Customising Prompts

All prompts are in the `polyphony/prompt_templates/` directory as editable YAML files:

```
polyphony/prompt_templates/
├── codebook_induction.yaml  # How AIs generate candidate codes
├── open_coding.yaml         # How AIs assign codes to segments (inductive)
├── deductive_coding.yaml    # How AIs assign codes strictly (deductive)
├── discussion.yaml          # How AIs explain disagreements
└── memo_synthesis.yaml      # How AIs synthesise themes
```

Variables use `$variable_name` syntax. Edit these files to adjust the AI's behaviour without touching any Python code. The files are also included in the replication package, so readers know exactly what instructions the AIs received.

---

## For Social Scientists New to Command-Line Tools

If you are not used to working in the terminal, here is a minimal workflow:

1. Open your terminal
2. Navigate to your project folder: `cd ~/Desktop/my_study`
3. Run commands starting with `polyphony ...`
4. At any point, run `polyphony --help` or `polyphony <command> --help` for guidance

All interactive steps (codebook review, flag resolution, memo writing) use a friendly interface in the terminal. No coding experience is required beyond running the commands above.

---

## Citation

If you use polyphony in published research, please cite it:

```
Alex Newhouse (2026). polyphony: Collaborative qualitative data analysis with
human and LLM coders. Software. https://github.com/alexbnewhouse/polyphony
```

---

## License

MIT License. See LICENSE file.
