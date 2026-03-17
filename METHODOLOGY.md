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
The human researcher acts as lead coder, supervisor, and final arbiter.

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

---

## Inductive vs. Deductive Coding

polyphony prioritises **inductive** (bottom-up) codebook development:

1. Agents read a sample of the data and propose codes grounded in what they observe.
2. The human researcher reviews and refines these proposals.
3. The resulting codebook reflects the data, not a pre-existing theoretical framework.

This follows the logic of Grounded Theory (Glaser & Strauss 1967; Charmaz 2006)
and Reflexive Thematic Analysis (Braun & Clarke 2022).

Deductive coding (starting from a pre-existing codebook) is also supported:
use `polyphony codebook add` to enter codes directly, skipping the induction step.

---

## Inter-Rater Reliability

polyphony reports three reliability metrics:

### Krippendorff's Alpha (α)
- Recommended for QDA because it handles missing data and scales to more than
  two coders
- Range: -1 to 1 (1 = perfect, 0 = chance, <0 = systematic disagreement)
- Threshold for acceptability: **α ≥ 0.80** (Krippendorff 2004); some
  accept **α ≥ 0.67** for exploratory work

### Cohen's Kappa (κ)
- Pairwise reliability accounting for chance agreement
- Range: -1 to 1
- Threshold: **κ ≥ 0.80** (strong); **κ ≥ 0.60** (moderate)

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

---

## The Calibration Loop

The calibration loop in polyphony mirrors the "norming" process in human coder studies:

1. Both agents code the same small set of segments.
2. IRR is computed.
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

## Limitations

1. **AI coders are not human coders.** Language models do not have lived
   experience, cultural knowledge, or interpretive creativity in the same sense
   as human researchers. High AI-AI IRR does not guarantee valid interpretation.

2. **Prompt sensitivity.** AI coding decisions depend heavily on prompt wording.
   Prompts should be treated as methodological choices and reported as such.
   polyphony stores all prompt text for this reason.

3. **Model version matters.** The same model name (e.g. "llama3.1") may refer
   to different weights at different times. polyphony records the Ollama manifest
   digest to ensure exact reproducibility.

4. **Saturation is approximate.** The saturation check in polyphony is a heuristic
   (declining rate of new codes) and should not replace theoretical judgment
   about when the corpus is sufficient.

5. **Confidentiality.** Ollama runs locally — your data never leaves your machine.
   However, if you use a cloud API instead, ensure you have appropriate
   data-sharing agreements.

---

## Reporting Guidelines

When reporting findings from a polyphony-assisted study, we recommend including:

- Model name and version (digest) for both coders
- Prompt templates used (include in supplementary materials or replication package)
- Temperature and seed settings
- IRR metrics (α, κ, % agreement) at each stage
- Number of calibration rounds and how disagreements were resolved
- A statement on the role of AI coders vs. human judgment in the final analysis

---

## Glossary

| Term | Definition |
|------|-----------|
| **Agent** | An AI model (or human) assigned a coder role in polyphony. Each project has Coder A, Coder B, and a Supervisor (you). |
| **Assignment** | The act of applying a code to a segment. One segment can receive multiple assignments. |
| **Axial coding** | A stage in grounded theory where open codes are grouped into categories with properties and dimensions. |
| **Calibration** | A structured exercise where both agents code the same sample of segments, then disagreements are reviewed to align their interpretations before full coding. |
| **Code** | A label applied to a segment that captures a concept, theme, or pattern. Codes have names, descriptions, and optional inclusion/exclusion criteria. |
| **Codebook** | The complete set of codes with their definitions. polyphony tracks multiple versions as the codebook evolves during analysis. |
| **Codebook induction** | The process of generating candidate codes from the data rather than specifying them in advance. polyphony supports LLM-assisted induction. |
| **Flag** | A marker on a segment indicating it needs attention — because of ambiguity, a coder disagreement, or a supervisor note. |
| **Grounded theory** | A methodology in which theory is developed inductively from the data through open, axial, and selective coding. |
| **Inter-rater reliability (IRR)** | A measure of how consistently two coders have applied the same codes to the same data. polyphony reports Krippendorff's alpha (primary), Cohen's kappa, and percent agreement. |
| **Krippendorff's alpha (α)** | The primary IRR metric. Ranges from 0 (chance agreement) to 1 (perfect agreement). Values ≥ 0.80 are conventionally acceptable for publication. |
| **Cohen's kappa (κ)** | An IRR metric that adjusts for chance agreement. Reported per code in polyphony. |
| **Memo** | A written note capturing theoretical insights, methodological decisions, or analytic observations during the research process. |
| **Open coding** | The first stage of coding, in which concepts are identified and labelled without predetermined categories. |
| **Replication package** | A directory generated by `polyphony export replication` containing all materials needed to verify or reproduce the analysis. |
| **Saturation** | Theoretical saturation is reached when new data no longer introduces new codes. polyphony estimates this by tracking the rate of new-code emergence. |
| **Segment** | A unit of text extracted from a document — the basic unit of coding. Can be a paragraph, a group of sentences, or a fixed word window. |
| **Selective coding** | The final stage of grounded theory coding, integrating categories around a core category. |
| **Seed** | A number that controls the randomness of an AI model's output. Using a fixed seed (with fixed temperature) produces reproducible results. |
| **Slug** | A short, URL-friendly identifier for a project derived from its name (e.g. "Housing Precarity Study 2026" → "housing-precarity-study-2026"). Used to open projects from the command line. |
| **Thematic analysis** | A methodology for identifying, analysing, and reporting patterns (themes) across qualitative data. |

---

## References

Braun, V., & Clarke, V. (2022). *Thematic Analysis: A Practical Guide*. SAGE.

Charmaz, K. (2006). *Constructing Grounded Theory*. SAGE.

Glaser, B. G., & Strauss, A. L. (1967). *The Discovery of Grounded Theory*. Aldine.

Krippendorff, K. (2004). *Content Analysis: An Introduction to Its Methodology* (2nd ed.). SAGE.

Lombard, M., Snyder‐Duch, J., & Bracken, C. C. (2002). Content analysis in
mass communication. *Human Communication Research*, 28(4), 587–604.

Neuendorf, K. A. (2002). *The Content Analysis Guidebook*. SAGE.
