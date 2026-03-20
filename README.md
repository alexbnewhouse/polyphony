# polyphony — Collaborative Qualitative Data Analysis

**polyphony** is a command-line tool for conducting rigorous qualitative data analysis (QDA) with two local AI language models working alongside you as independent coders. You can also participate as a full third coder for 3-way inter-rater reliability, lead codebook induction yourself, or code a representative sample while the LLMs code the full corpus. It supports both text and image data, enabling visual QDA on photographs, diagrams, screenshots, and other visual materials alongside traditional text analysis. It is designed for solo social scientists who want the analytical benefits of multi-coder studies without a full research team.

---

## Overview

In a traditional multi-coder QDA study, two or more researchers independently code the same data, then discuss disagreements until they reach agreement. polyphony replicates this workflow:

- **You** act as lead researcher and supervisor
- **Coder A** and **Coder B** are two local AI models (via [Ollama](https://ollama.ai))
- The system guides you through every stage: codebook design → calibration → coding → reliability → discussion → analysis → export

All model calls are logged with full prompts, responses, model versions, temperature, and seed — so every analytical decision is fully reproducible.

---

## Key Features

- **Inductive codebook design**: Both AIs suggest codes from a sample; you review and approve. With `--human-leads`, you propose codes first.
- **Human-as-lead-coder**: Optionally code as a full third coder alongside the two LLMs for 3-way IRR, reducing correlated LLM bias
- **Calibration**: Structured rounds to align coders before full analysis, with optional 3-way calibration (`--include-supervisor`)
- **Independent coding**: Agents code without seeing each other's work. The supervisor can code all segments or a representative sample (`--sample-size`)
- **Inter-rater reliability**: Krippendorff's alpha (2-way and 3-way), pairwise Cohen's kappa, percent agreement
- **Flag & discussion system**: Ambiguous cases surface for structured debate
- **Analytical memos**: Write theoretical/methodological notes throughout
- **Multimodal image support**: Import and code images (PNG, JPEG, GIF, WebP, BMP, TIFF) alongside text using vision-capable models
- **Full replication package**: Every prompt, response, and decision is exportable
- **Supports multiple methodologies**: Grounded theory, thematic analysis, content analysis

---

## Quick Start

### 1. Install

```bash
# Install polyphony
pip install polyphony

# Install Ollama (see https://ollama.ai)
# Then pull a model:
ollama pull llama3.1:8b
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
polyphony project new --name "My Study" --model-a llama3.1:8b --model-b llama3.2:3b
```

The default models are `llama3.1:8b` for both coders unless overridden.

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

# Images (requires a vision-capable model — see Multimodal below)
polyphony data import photos/*.jpg
polyphony data import screenshots/*.png diagrams/*.webp
```

### 4. Induce a codebook

```bash
polyphony codebook induce --sample-size 20
```

Both AIs read a 20-segment sample and propose codes. You review each candidate: accept, reject, rename, or edit definitions.

To lead codebook development yourself (proposing codes before seeing LLM suggestions):

```bash
polyphony codebook induce --human-leads
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
polyphony code run
```

Both agents code the full corpus independently. Neither sees the other's work.

To code as a third coder yourself (all three coders):

```bash
polyphony code run --agent all                   # you code everything alongside the AIs
polyphony code run --agent all --sample-size 50   # you code 50 segments, AIs code all
```

### 7. Compute reliability

```bash
polyphony irr compute                # 2-way (A vs B)
polyphony irr compute --three-way    # 3-way (A vs B vs supervisor) with pairwise kappa table
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

---

## Multimodal Image Support

polyphony can analyse images alongside text documents. Vision-capable Ollama models (e.g. `llava`, `llama3.2-vision`) code image segments through the standard multimodal message API — the same pipeline stages apply: codebook induction, calibration, independent coding, discussion, and export.

### Setup

```bash
# Pull a vision-capable model
ollama pull llava

# Create a project using a vision model for at least one coder
polyphony project new --name "Visual Study" --model-a llava --model-b llava

# Optional: install Pillow for image dimension metadata
pip install polyphony[images]
```

### How it works

- **Supported formats**: PNG, JPEG, GIF, WebP, BMP, TIFF
- **Import**: Each image becomes a single-segment document with `media_type='image'`. Images are copied to `<project_dir>/images/` with a content-hash prefix for deduplication.
- **Coding**: When the pipeline encounters an image segment, it sends the image to the vision model alongside the coding prompt. Prompt templates include visual analysis instructions automatically.
- **Mixed corpora**: You can import text and images into the same project. Text segments are coded by text models; image segments require a vision-capable model.
- **Replication**: Image file paths are logged in the LLM audit trail. The replication package includes copies of all images and an image count in the manifest.

### Example workflow

```bash
polyphony data import fieldwork_photos/*.jpg
polyphony data list                          # media_type column shows 'image' vs 'text'
polyphony codebook induce --sample-size 10   # vision model describes what it sees
polyphony code run                           # images coded alongside text
polyphony code show 42                       # displays image path for image segments
```

---

## Human-as-Lead-Coder

By default, polyphony runs the two LLM agents as coders while you act as supervisor. The human-as-lead-coder mode makes you a full third coder, directly addressing three methodological concerns:

1. **Correlated LLM bias**: Two LLMs may share systematic blind spots that inflate IRR. Adding a human coder breaks this correlation.
2. **Interpretive sensitivity**: Your domain expertise and lived understanding of the data are captured directly in coding decisions, not just in supervisory review.
3. **Rubber-stamping risk**: As a third coder rather than a reviewer, you engage with the data at the same level as the AI agents.

### Three coding modes

| Mode | Command | Human codes | LLMs code | IRR |
|------|---------|------------|-----------|-----|
| **Default** (supervisor only) | `polyphony code run` | — | All segments | 2-way (A vs B) |
| **Full 3-way** | `polyphony code run --agent all` | All segments | All segments | 3-way |
| **Sample** (practical default) | `polyphony code run --agent all --sample-size 50` | 50 random segments | All segments | 3-way on intersection |

For large corpora, the sample mode is recommended. Krippendorff's alpha natively handles partial data, so IRR is computed on the segments all three coders coded.

---

## Project Directory Structure

Each project is stored in `~/.polyphony/projects/<slug>/`:

```
~/.polyphony/projects/housing-precarity-2026/
├── project.db          # Single SQLite file containing everything
└── images/             # Imported images (created when images are imported)
```

A `.polyphony_project` marker file in your working directory points to the active project.

---

## Full Command Reference

```
polyphony project new          Create a new project
polyphony project open         Set active project
polyphony project list         List all projects
polyphony project status       Show pipeline status and counts

polyphony data import          Import documents (txt, csv, json, docx)
polyphony data list            List imported documents
polyphony data show            Display a document or its segments

polyphony codebook induce      AI-assisted codebook induction (--human-leads)
polyphony codebook show        Display codebook as a tree
polyphony codebook add         Add a code manually
polyphony codebook edit        Edit a code in $EDITOR
polyphony codebook finalize    Mark codebook as final
polyphony codebook history     Show all codebook versions

polyphony calibrate run        Run calibration round(s) (--include-supervisor)

polyphony code run             Run independent coding (--agent all, --sample-size)
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
- [Ollama](https://ollama.ai) running locally with at least one model installed
- ~4 GB RAM per model (less for quantized versions)

### Python dependencies

```
click, rich, pydantic, ollama, krippendorff, scikit-learn, numpy, pandas, PyYAML, python-docx
```

**Optional** (for image metadata extraction):

```bash
pip install polyphony[images]   # installs Pillow>=10.0
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
Make sure you are using a vision-capable model (`llava`, `llama3.2-vision`, etc.). Standard text models cannot process images. Check your model with `ollama show <model>` and look for multimodal capabilities.

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
- **Model name and digest** (exact model weights, via Ollama manifest)
- **Seed and temperature** (for deterministic reproduction)
- **Full system and user prompts** (including the complete codebook version)
- **Full response text** and parsed output
- **Timestamps**

The `polyphony export replication` command packages all of this into a self-contained directory with scripts to verify checksums and re-run individual calls.

---

## Customising Prompts

All prompts are in the `polyphony/prompt_templates/` directory as editable YAML files:

```
polyphony/prompt_templates/
├── codebook_induction.yaml  # How AIs generate candidate codes
├── open_coding.yaml         # How AIs assign codes to segments
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
