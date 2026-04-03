# Methodological Notes for polyphony

This document explains the methodological rationale behind polyphony's design.
It is aimed at qualitative researchers evaluating whether this tool is appropriate
for their study, and at reviewers assessing the validity of studies that used it.

---

## The Multi-Coder Paradigm

Traditional QDA typically involves two or more human coders who independently
code the same data, then compare their work. This serves three purposes:

1. **Reducing individual bias**: A single coder's interpretive framework shapes
   what they notice and how they label it. A second coder provides a check.
2. **Demonstrating reproducibility**: High inter-rater reliability (IRR) suggests
   the coding scheme captures something systematic in the data rather than being
   idiosyncratic.
3. **Surfacing ambiguity**: Disagreements are analytically valuable — they reveal
   segments where the data is genuinely uncertain or where the codebook is under-specified.

polyphony replaces the second (and optionally third) human coder with two local AI models.
The human researcher acts as lead coder, supervisor, and final arbiter — and can
optionally participate as a full third coder for 3-way inter-rater reliability.

---

## Practice Sandbox Workflow

polyphony includes an offline-first `practice` workflow for training before live analysis.

- From the **CLI**, practice mode generates synthetic interview segments from template-based
  domains (no model calls required). Researchers can optionally practice with real local
  files (`--source-file`) in an isolated sandbox project. LLM-generated synthetic practice
  data is also available (`--topic`) when Ollama is installed, but this is opt-in.
- From the **web GUI**, a one-click "Create Practice Project" button on the home page
  provisions a sample project with fictional housing-insecurity interview data. This
  lets researchers walk through the full GUI workflow (import → codebook → calibration →
  coding → IRR → export) without configuring models or supplying their own data first.

Methodologically, this separates workflow training from substantive analysis. It helps
research teams standardize coding procedure, memo conventions, and disagreement review
before they touch the real corpus.

From a software-validity perspective, the implementation is backed by scenario-based
integration tests covering calibration and coding orchestration (for example,
resume behavior, superseding incomplete runs, and threshold-driven calibration exits)
plus adversarial negative controls for key safety constraints.

---

## Audio Transcription as a Pre-Ingest Layer

polyphony now supports direct audio ingestion through `polyphony data transcribe`.
Methodologically, this is treated as a **pre-ingest transformation step**:

1. Source audio is copied into the project for provenance.
2. A transcription backend (local Whisper or OpenAI transcription) produces text.
3. Transcript text is segmented and coded through the same text-native pipeline.

This design intentionally avoids a separate "audio coding" prompt branch in early
phases. It keeps coding semantics stable and reuses the validated text workflow
for induction, calibration, coding, IRR, discussion, and export.

### Validity implications

- **Transcription quality affects downstream coding quality.** Mis-transcribed speech,
  speaker overlap, and domain-specific jargon can propagate into coding decisions.
- **Language hints should be explicit** when known (`--language`) to reduce
  transcription drift.
- **Research teams should spot-check transcript fidelity** (for example, random
  manual audits against the source audio) before interpreting low-frequency codes.

### Reproducibility implications

Transcribed documents carry provenance metadata (provider, model, source audio path,
hash, and language hints). Replication exports include these metadata plus copied
source audio artifacts when available, so reviewers can audit transformation from
audio to coded text.

---

## RSS/Atom Feed Ingestion

polyphony also supports importing textual data from RSS/Atom feeds using
`polyphony data rss preview` and `polyphony data rss import`.

Methodologically, this acts as a **bounded sampling interface** for publicly
syndicated sources (news posts, blogs, newsletters, policy bulletins):

1. Researchers preview candidate entries.
2. They choose explicit entries/ranges to include.
3. Selected entries are imported as text documents and segmented normally.

### Why explicit selection matters

- Feed ordering is often algorithmic or publisher-controlled.
- Automatic "import all" can over-represent bursty publication periods.
- Manual/explicit selection keeps inclusion criteria auditable.

Researchers should define and memo their feed sampling rules (time windows,
keywords, publication types, and exclusion criteria) before coding.

### Validity caveats

- Feed text can be abbreviated summaries rather than full articles.
- HTML cleanup can remove layout/contextual cues.
- Publication metadata quality varies by source.

These are not software bugs; they are source-data properties that should be
reported as limitations in methods sections.

### Reproducibility implications

Imported feed entries include provenance metadata (feed URL, entry GUID/link,
timestamp fields, author, tags, and selected content source). This allows
reviewers to trace each coded segment back to the syndicated source snapshot
that was available during ingestion.

---

## Podcast Ingestion Pipeline

polyphony provides an end-to-end pipeline for podcast research via
`polyphony data podcast preview`, `polyphony data podcast download`, and
`polyphony data podcast ingest`. This is designed for social scientists
studying podcasts as qualitative data sources.

### Pipeline stages

1. **Feed preview with safety estimates.** The `preview` command fetches the
   RSS feed (with full iTunes/podcast namespace parsing), displays episode
   metadata (season/episode numbers, durations, file sizes), and estimates
   total download size and disk space requirements.
2. **Audio download.** Episodes are downloaded with configurable per-episode
   (default 500 MB) and total (default 5 GB) size limits, SSRF protections,
   and disk space verification.
3. **Transcription with optional diarization.** Each episode is transcribed
   using local Whisper or OpenAI. When `--diarize` is enabled, pyannote.audio
   identifies individual speakers and labels each segment.
4. **Timestamp-preserving import.** Whisper segments are imported with their
   audio timestamps (`audio_start_sec`, `audio_end_sec`) and speaker labels
   preserved on each database segment, enabling time-aligned analysis.

### Speaker diarization

Speaker diarization (identifying who spoke when) uses pyannote.audio and
requires a Hugging Face access token (`HF_TOKEN`). It is treated as an
optional enrichment layer:

- Diarization runs post-transcription and assigns speaker labels to Whisper
  segments based on temporal overlap.
- When using `speaker_turn` segmentation (the default for podcast ingest),
  consecutive same-speaker Whisper segments are merged into turns,
  preserving the first segment's start time and the last's end time.
- If diarization dependencies are missing or fail, transcription proceeds
  without speaker labels — it degrades gracefully rather than failing.

### Segmentation strategy for podcasts

The default segmentation for `podcast ingest` is `speaker_turn`, which
splits the transcript at speaker changes. This produces analytically
meaningful units (each speaker's contribution as a segment) rather than
arbitrary paragraph or word-count boundaries.

Researchers can override this with `--segment-by paragraph` or other
strategies if speaker-turn granularity is not appropriate for their study.

### Per-episode and per-speaker analysis

After coding, `polyphony analyze frequencies-by-doc` shows code distributions
broken down by document (i.e., per episode), and `polyphony analyze speaker-codes`
shows code distributions broken down by speaker label. These are essential for
comparative podcast analysis — for example, comparing how hosts and guests
are coded differently, or tracking thematic shifts across episodes.

### Validity caveats

- **Diarization accuracy** depends on audio quality, speaker overlap, and the
  number of speakers. Researchers should spot-check speaker assignments,
  especially for episodes with cross-talk or background noise.
- **Transcription quality** is the primary determinant of coding quality.
  Domain-specific jargon, accents, and low-quality audio can introduce
  systematic transcription errors that propagate into coding.
- **Episode metadata quality** varies by podcast publisher. Not all feeds
  include enclosure sizes, episode numbers, or duration metadata.
- **RSS feeds are snapshots.** Feed content may change over time as publishers
  add, remove, or modify episodes. Record the feed URL and ingestion date.

### Reproducibility implications

Each imported episode carries provenance metadata including: feed URL, feed
title, episode title, season/episode numbers, publication date, show author,
transcription provider/model, diarization status, and speaker count. Audio
files are stored in the project's `audio/` directory. The replication package
includes all of this for full audit trail from RSS feed to coded segments.

---

## Why Two AI Coders?

Using two models (with different seeds) rather than one serves the same purpose
as having two human coders:

- **Independence**: Each model receives the same prompt but generates different outputs
  due to different random seeds. Neither sees the other's work during coding.
- **Disagreements**: When the two models disagree, this flags segments that are
  genuinely ambiguous or under-specified — exactly the kind of analytical traction
  you want.
- **Calibration**: Running both models on a calibration set before full coding lets
  you identify and resolve ambiguities in the codebook before investing in the
  full analysis.

You can use the same base model for both coders (different seeds) or two
different models (e.g. Llama and Mistral) for greater independence.

### Human as Third Coder

Two AI coders may share correlated biases — systematic blind spots that both
models reproduce due to shared training data. This can inflate IRR by
producing agreement that reflects model similarity rather than codebook clarity.

polyphony addresses this by allowing the human researcher to code as a full
third coder (`--agent all`). This:

- **Breaks correlated bias**: Human interpretive judgment is independent of LLM
  training artifacts.
- **Captures interpretive sensitivity**: The researcher's domain knowledge and
  contextual understanding are recorded in coding data, not just supervisory
  review.
- **Enables 3-way IRR**: Krippendorff's alpha computed across all three coders
  is a more robust measure than pairwise agreement between two LLMs.

For large corpora, the human can code a representative sample (`--sample-size N`)
while LLMs code everything. Krippendorff's alpha natively handles partial data,
so IRR is computed on the intersection of segments coded by all three.

---

## Inductive vs. Deductive Coding

polyphony supports both **inductive** (bottom-up) and **deductive** (top-down)
codebook development:

### Inductive coding

1. Agents read a sample of the data and propose codes grounded in what they observe.
2. The human researcher reviews and refines these proposals.
3. The resulting codebook reflects the data, not a pre-existing theoretical framework.

With **human-led induction** (`--human-leads`), the researcher proposes codes first
from the sample segments, then sees LLM suggestions merged in. This ensures the
human's interpretive lens shapes the codebook from the start rather than being
limited to accepting or rejecting LLM proposals.

This follows the logic of Grounded Theory (Glaser & Strauss 1967; Charmaz 2006)
and Reflexive Thematic Analysis (Braun & Clarke 2022).

### Deductive coding

For theory-driven research, polyphony supports importing a pre-existing codebook
directly from YAML, JSON, or CSV:

```bash
polyphony codebook import my_codebook.yaml
polyphony codebook import --finalize theoretical_framework.csv
```

When used with `polyphony code run --deductive`, the AI coders apply the imported
codebook strictly — they are instructed not to suggest new codes or flag missing
categories. This is appropriate when the codebook represents a theoretical
framework established prior to data collection (e.g., a validated coding scheme
from prior literature, or a content analysis framework).

The deductive workflow follows the logic of directed content analysis
(Hsieh & Shannon 2005) and deductive thematic analysis (Braun & Clarke 2022).

---

## Inter-Rater Reliability

polyphony reports three reliability metrics:

### Krippendorff's Alpha (α)
- Recommended for QDA because it handles missing data and scales to more than
  two coders
- Range: -1 to 1 (1 = perfect, 0 = chance, <0 = systematic disagreement)
- Threshold for acceptability: **α ≥ 0.80** (Krippendorff 2004); some
  accept **α ≥ 0.67** for exploratory work
- polyphony reports both **2-way alpha** (A vs B) and **3-way alpha** (A vs B vs
  supervisor) when the human codes as a third coder

### Cohen's Kappa (κ)
- Pairwise reliability accounting for chance agreement
- Range: -1 to 1
- Threshold: **κ ≥ 0.80** (strong); **κ ≥ 0.60** (moderate)
- In 3-way mode, polyphony reports a **pairwise kappa table** for all
  coder pairs (A–B, A–supervisor, B–supervisor)

### Percent Agreement
- Simple baseline: proportion of segments coded identically
- Does not account for chance; included for transparency only

For **multi-label coding** (a segment can receive multiple codes), polyphony uses
a binary present/absent scheme per code: for each code, did both coders assign it
or not? This is averaged across all codes.

### Important caveat

High IRR with AI coders has a different meaning than high IRR with human coders.
It indicates that your codebook is unambiguous enough for a language model to
apply consistently. It does not replace human interpretive judgment — it
supplements it. Disagreements between AI coders should be treated as prompts
for deeper human analysis, not as errors to be corrected.

When the human codes as a third coder, 3-way alpha provides a stronger validity
signal: it measures whether the codebook produces consistent results across
fundamentally different types of coders (human + LLM), not just between two
instances of the same model family.

---

## The Calibration Loop

The calibration loop in polyphony mirrors the "norming" process in human coder studies:

1. Both agents (or all three, with `--include-supervisor`) code the same small set of segments.
2. IRR is computed (2-way or 3-way).
3. If IRR is below the threshold, disagreements are reviewed:
   - Each agent explains its reasoning for disagreements.
   - The human supervisor adjudicates and, if needed, refines code definitions.
4. Updated codebook → repeat coding → recompute IRR.
5. Once IRR is acceptable, proceed to full coding.

This iterative process is standard in multi-coder studies and is supported by
Neuendorf (2002), Lombard et al. (2002), and Krippendorff (2004).

---

## Independence Enforcement

A critical methodological requirement is that coders work independently.
polyphony enforces this at the software level:

- During a coding run, each agent receives only: (1) the codebook, (2) the
  target segment. It never receives the other agent's output.
- The database stores assignments per agent per run — cross-contamination is
  structurally impossible until the IRR phase.
- Seeds are fixed per agent, so results are deterministic and the independence
  of each agent's perspective is stable across reruns.

---

## Prompt Sensitivity

AI coding decisions are sensitive to prompt wording. polyphony treats prompts
as methodological decisions and provides several mechanisms for transparency:

### How polyphony tracks prompt changes

1. **Full prompt logging.** Every LLM call records the complete system and
   user prompt in the `llm_call` table. The replication package includes all
   prompts as-sent.

2. **Prompt hashing.** Each LLM call records a SHA-256 hash of the combined
   system+user prompt. This allows researchers to verify that all coding calls
   used identical prompts — or to identify exactly when a prompt change occurred.

3. **Prompt template snapshots.** The replication package includes copies of
   all `.yaml` prompt templates as they existed at export time.

### Recommendations for managing prompt sensitivity

- **Freeze prompts before coding.** Edit prompt templates during calibration
  but do not change them between calibration and independent coding. If you do,
  re-calibrate.
- **Report prompt templates.** Include the exact templates in supplementary
  materials. Prompt wording is a methodological decision comparable to interview
  question design.
- **Run sensitivity checks.** If resources permit, run the same corpus with
  a minor prompt variation (e.g., rephrasing instructions) and compare IRR.
  A robust codebook should produce similar results across reasonable prompt
  variations.
- **Use deductive mode for stability.** The deductive coding prompt
  (`--deductive`) is deliberately simpler and more constrained than the inductive
  prompt, reducing the surface area for prompt sensitivity.
- **Compare across models.** Using different models for Coder A and Coder B
  (e.g., Llama vs Mistral) tests whether results depend on a specific model's
  interpretation of the prompt.

### What prompt sensitivity means for validity

High IRR across prompt variations or model combinations strengthens the claim
that the codebook — not the prompt wording — is driving coding decisions.
Conversely, if minor prompt changes substantially alter results, the codebook
may be under-specified and needs refinement.

---

## Scale and Performance

### Corpus size guidelines

polyphony processes segments sequentially per agent to avoid GPU contention
and keep SQLite writes simple. Practical performance depends on model size,
hardware, and segment length.

| Corpus size | Segments | Approximate time (8B model, GPU) | Notes |
|------------|----------|----------------------------------|-------|
| Small | < 200 | Minutes | Full 3-way human coding practical |
| Medium | 200–2,000 | 1–4 hours | Use `--sample-size` for human coding |
| Large | 2,000–10,000 | 4–24 hours | Consider smaller/faster models |
| Very large | > 10,000 | Days | Batch in stages; use quantized models |

### Practical tips for large corpora

- **Use sampling for human coding.** With `--sample-size 50`, the human codes
  50 randomly selected segments while LLMs code everything. Krippendorff's alpha
  handles partial data natively.
- **Use smaller models for calibration.** Calibrate with a fast model
  (e.g., `llama3.2:3b`), then switch to a larger model for independent coding.
- **Batch by document.** For very large corpora, import and code in batches
  to manage memory and allow incremental review.
- **Monitor with `polyphony code status`.** Track coding progress per agent
  and estimate remaining time from completed segments.
- **Use `--resume` for interrupted runs.** If a coding session is interrupted
  (e.g., by a machine restart), `polyphony code run --resume` picks up where
  it left off without re-coding completed segments.
- **Use cloud APIs for speed.** With `--provider-a openai --model-a gpt-4o`,
  API-based models can be significantly faster than local inference for large
  corpora.

### Database performance

polyphony uses SQLite with WAL (Write-Ahead Logging) mode. The database can
comfortably handle projects with 100,000+ segments and millions of assignment
rows. Queries remain fast because indexes are defined on all frequently-joined
columns.

---

## Limitations

1. **AI coders are not human coders.** Language models do not have lived
   experience, cultural knowledge, or interpretive creativity in the same sense
   as human researchers. High AI-AI IRR does not guarantee valid interpretation.
   The human-as-lead-coder mode mitigates this by including a human perspective
   in the reliability measurement, but it does not eliminate the limitation.

2. **Prompt sensitivity.** AI coding decisions depend heavily on prompt wording.
   Prompts should be treated as methodological choices and reported as such.
   polyphony stores all prompt text and prompt hashes for this reason.
   See the Prompt Sensitivity section above.

3. **Model version matters.** The same model name (e.g. "llama3.1") may refer
   to different weights at different times. polyphony records the model
   digest to ensure exact reproducibility.

4. **Saturation is approximate.** The saturation check in polyphony is a heuristic
  (three consecutive coding windows with zero new codes) and should not
  replace theoretical judgment about when the corpus is sufficient.

5. **Confidentiality.** Ollama runs locally — your data never leaves your machine.
   When using cloud API providers (OpenAI, Anthropic), your data is sent to
   external servers. Ensure you have appropriate data-sharing agreements and
   IRB approval before using cloud APIs with sensitive data.

6. **Seed behaviour varies by provider.** Ollama's seed support varies by model.
   OpenAI's seed parameter is best-effort. Anthropic does not support seeds.
   polyphony records the seed setting regardless, but exact reproducibility
   depends on the provider and model.

---

## Reporting Guidelines

When reporting findings from a polyphony-assisted study, we recommend including:

- Model name, version (digest), and provider for both coders
- Whether coding was inductive or deductive (`--deductive`)
- Prompt templates used (include in supplementary materials or replication package)
- Temperature and seed settings
- IRR metrics (α, κ, % agreement) at each stage — including 3-way alpha and pairwise kappas if the human coded as a third coder
- If the human coded a sample rather than the full corpus, the sample size, seed, and the number of segments in the IRR intersection
- Number of calibration rounds and how disagreements were resolved
- Corpus size (documents, segments) and approximate processing time
- Any prompt modifications made during the study
- A statement on the role of AI coders vs. human judgment in the final analysis
- Whether results were obtained through the CLI or the web GUI (both share the same
  underlying pipeline and database, so analytical validity is identical)
- For deductive studies: the source and validation status of the imported codebook
- For podcast/audio studies: transcription provider and model, whether diarization
  was used, speaker count, and any spot-check findings on transcription accuracy

---

## Glossary

| Term | Definition |
|------|-----------|
| **Agent** | An AI model (or human) assigned a coder role in polyphony. Each project has Coder A, Coder B, and a Supervisor (you). With `--agent all`, the supervisor also acts as a third independent coder. |
| **Assignment** | The act of applying a code to a segment. One segment can receive multiple assignments. |
| **Axial coding** | A stage in grounded theory where open codes are grouped into categories with properties and dimensions. |
| **Calibration** | A structured exercise where both agents code the same sample of segments, then disagreements are reviewed to align their interpretations before full coding. |
| **Code** | A label applied to a segment that captures a concept, theme, or pattern. Codes have names, descriptions, and optional inclusion/exclusion criteria. |
| **Codebook** | The complete set of codes with their definitions. polyphony tracks multiple versions as the codebook evolves during analysis. |
| **Codebook induction** | The process of generating candidate codes from the data rather than specifying them in advance. polyphony supports both LLM-assisted induction and human-led induction (`--human-leads`). |
| **Deductive coding** | Applying a pre-existing theoretical codebook to data, as opposed to generating codes from the data. Enabled with `polyphony codebook import` and `polyphony code run --deductive`. |
| **Diarization** | The process of identifying which speaker is speaking at each point in an audio recording. polyphony uses pyannote.audio for speaker diarization, producing `[SPEAKER_0]`, `[SPEAKER_1]`, etc. labels on transcript segments. |
| **Flag** | A marker on a segment indicating it needs attention — because of ambiguity, a coder disagreement, or a supervisor note. |
| **GUI** | The optional Streamlit-based web interface (`polyphony-gui`). Provides the same analytical workflow as the CLI through a browser-based point-and-click interface. Installed via `pip install polyphony[gui]`. |
| **Grounded theory** | A methodology in which theory is developed inductively from the data through open, axial, and selective coding. |
| **Inter-rater reliability (IRR)** | A measure of how consistently coders have applied the same codes to the same data. polyphony reports Krippendorff's alpha (primary, 2-way and 3-way), pairwise Cohen's kappa, and percent agreement. |
| **Krippendorff's alpha (α)** | The primary IRR metric. Ranges from 0 (chance agreement) to 1 (perfect agreement). Values ≥ 0.80 are conventionally acceptable for publication. In 3-way mode, alpha is computed across all three coders natively. |
| **Cohen's kappa (κ)** | An IRR metric that adjusts for chance agreement. Reported per code and, in 3-way mode, for all coder pairs. |
| **Memo** | A written note capturing theoretical insights, methodological decisions, or analytic observations during the research process. |
| **Open coding** | The first stage of coding, in which concepts are identified and labelled without predetermined categories. |
| **Replication package** | A directory generated by `polyphony export replication` containing all materials needed to verify or reproduce the analysis. |
| **Saturation** | Theoretical saturation is reached when new data no longer introduces new codes. polyphony estimates this by tracking the rate of new-code emergence. |
| **Segment** | A unit of text extracted from a document — the basic unit of coding. Can be a paragraph, a group of sentences, a fixed word window, or a speaker turn (for diarized audio). |
| **Selective coding** | The final stage of grounded theory coding, integrating categories around a core category. |
| **Seed** | A number that controls the randomness of an AI model's output. Using a fixed seed (with fixed temperature) produces reproducible results. |
| **Slug** | A short, URL-friendly identifier for a project derived from its name (e.g. "Housing Precarity Study 2026" → "housing-precarity-study-2026"). Used to open projects from the command line. || **Speaker turn** | A segmentation strategy for diarized audio transcripts that splits text at speaker changes. Each speaker's consecutive utterance becomes one segment, preserving audio timestamps. || **Thematic analysis** | A methodology for identifying, analysing, and reporting patterns (themes) across qualitative data. |

---

## References

Braun, V., & Clarke, V. (2022). *Thematic Analysis: A Practical Guide*. SAGE.

Charmaz, K. (2006). *Constructing Grounded Theory*. SAGE.

Glaser, B. G., & Strauss, A. L. (1967). *The Discovery of Grounded Theory*. Aldine.

Hsieh, H.-F., & Shannon, S. E. (2005). Three approaches to qualitative content
analysis. *Qualitative Health Research*, 15(9), 1277–1288.

Krippendorff, K. (2004). *Content Analysis: An Introduction to Its Methodology* (2nd ed.). SAGE.

Lombard, M., Snyder‐Duch, J., & Bracken, C. C. (2002). Content analysis in
mass communication. *Human Communication Research*, 28(4), 587–604.

Neuendorf, K. A. (2002). *The Content Analysis Guidebook*. SAGE.
