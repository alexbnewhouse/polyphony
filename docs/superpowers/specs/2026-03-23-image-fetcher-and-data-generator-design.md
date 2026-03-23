# Image URL Fetcher & Fake Data Generator — Design Spec

**Date:** 2026-03-23
**Status:** Approved

## Overview

Two new capabilities for polyphony:

1. **CSV Image URL Fetcher** — Download images from URLs listed in a CSV and import them as multimodal documents
2. **Fake Data Generator** — Generate realistic synthetic QDA datasets for training and practice

## Feature 1: CSV Image URL Fetcher

### CLI Interface

```bash
polyphony data fetch-images urls.csv --url-column "image_url" --metadata-columns "caption,source"
```

**Arguments:**
- `csv_path` (required): Path to CSV file containing image URLs
- `--url-column` (default: `"url"`): Column name containing image URLs
- `--metadata-columns`: Comma-separated list of columns to preserve as document metadata
- `--timeout` (default: 30): Per-image download timeout in seconds
- `--max-concurrent` (default: 5): Maximum concurrent downloads

### Architecture

**New file: `polyphony/io/fetchers.py`**

Responsibilities:
- Parse CSV and extract URLs
- Download images concurrently using `urllib.request` + `concurrent.futures.ThreadPoolExecutor`
- Save to `<project_dir>/images/` with SHA256-based deduplication (matching existing pattern)
- Return list of downloaded file paths with metadata

**Modified file: `polyphony/cli/cmd_data.py`**

New command `fetch_images` that:
1. Validates CSV and column existence
2. Calls fetcher to download images
3. Imports each downloaded image using existing `import_image()` from `importers.py`
4. Displays Rich progress bar and summary

### Data Flow

```
CSV file → parse URLs → download (concurrent) → save to images/ → import_image() → DB
```

### Error Handling

- Invalid URLs: skip with warning, continue processing
- Download failures: 1 retry, then skip with warning
- Duplicate images: SHA256 deduplication (existing pattern)
- Non-image content-type: skip with warning
- Network timeout: configurable, default 30s

### Constraints

- No new dependencies — uses `urllib.request` from stdlib
- Reuses existing `import_image()` for DB insertion
- Supports http and https URLs only

## Feature 2: Fake Data Generator

### CLI Interface

```bash
# Pre-built domains (template-based, no LLM)
polyphony data generate --domain housing --segments 30
polyphony data generate --domain healthcare --segments 50
polyphony data generate --domain education --segments 20

# List available domains
polyphony data generate --list-domains

# Custom topic (requires Ollama)
polyphony data generate --topic "climate anxiety among young adults" \
  --segments 25 --model llama3.2

# Export without importing
polyphony data generate --domain housing --segments 20 --output training_data.csv
```

**Arguments:**
- `--domain`: Pre-built domain name (housing, healthcare, education)
- `--topic`: Custom topic string (advanced, requires Ollama)
- `--model`: Ollama model for custom generation (default: project's model-a)
- `--segments` (default: 20): Number of segments to generate
- `--list-domains`: Show available pre-built domains
- `--output`: Export to file instead of importing into project
- `--seed`: Random seed for reproducibility

### Architecture

**New file: `polyphony/generators.py`**

Contains:

1. **Domain templates** — Dict of domain configs, each with:
   - ~40-50 interview excerpt templates with `{placeholder}` slots
   - Participant name/detail pools for randomization
   - Suggested codebook (codes + definitions)

2. **`generate_template_data(domain, n, seed)`** — Template-based generation:
   - Randomly samples and fills templates
   - Varies participant details, specifics, emotional register
   - Returns list of segment dicts with text + metadata

3. **`generate_llm_data(topic, n, model, ollama_host)`** — LLM-based generation:
   - Prompts Ollama to generate interview-style segments
   - Also generates a suggested codebook
   - Returns segments + codebook

4. **`get_domains()`** — Returns available domain names and descriptions

**Modified file: `polyphony/cli/cmd_data.py`**

New command `generate` that:
1. Validates domain or topic is provided (mutually exclusive)
2. Calls appropriate generator function
3. Either imports into active project or exports to file
4. Displays Rich summary of generated data

### Pre-built Domains

**Housing Precarity:**
- Themes: rent burden, eviction threat, substandard conditions, landlord conflict, homelessness risk, coping strategies
- Participant types: renters, families, individuals, elderly tenants
- Suggested codes: FINANCIAL_STRESS, HOUSING_INSTABILITY, LANDLORD_CONFLICT, COPING_STRATEGY, SOCIAL_SUPPORT, HEALTH_IMPACT

**Healthcare Access:**
- Themes: insurance gaps, cost barriers, delayed care, rural access, ER reliance, medication costs
- Participant types: uninsured workers, rural residents, chronic illness patients, parents
- Suggested codes: COST_BARRIER, DELAYED_CARE, INSURANCE_GAP, PROVIDER_SHORTAGE, HEALTH_OUTCOME, SYSTEM_NAVIGATION

**Education Equity:**
- Themes: funding disparities, resource access, teacher quality, family involvement, achievement gaps, school climate
- Participant types: students, parents, teachers, administrators
- Suggested codes: RESOURCE_INEQUALITY, FAMILY_ENGAGEMENT, TEACHER_QUALITY, ACHIEVEMENT_GAP, SCHOOL_CLIMATE, SYSTEMIC_BARRIER

### LLM Generation (Advanced)

Prompt structure:
```
You are generating realistic interview transcript excerpts for qualitative
data analysis training. Topic: {topic}

Generate {n} interview segments that:
- Sound like real interview transcripts (first person, conversational)
- Cover diverse perspectives and experiences
- Include emotional content, specific details, hedging, repetition
- Are 3-8 sentences each
- Would be suitable for coding with a qualitative codebook

Also suggest 5-7 qualitative codes with definitions.

Output as JSON: {"segments": [...], "codes": [...]}
```

### Error Handling

- Unknown domain: show available domains with `--list-domains`
- `--topic` without Ollama: clear error message about requirement
- LLM output parsing: JSON extraction with fallback
- Seed: deterministic output for template-based; passed to Ollama for LLM-based

## Testing

**`tests/test_fetchers.py`:**
- CSV parsing with various column configurations
- Download with mocked HTTP responses (unittest.mock)
- SHA256 deduplication
- Error handling (bad URLs, timeouts, non-images)

**`tests/test_generators.py`:**
- Template generation for each domain
- Segment count accuracy
- Seed reproducibility
- Domain listing
- LLM generation with mocked Ollama responses
- Output format validation

## Files Changed

| File | Change |
|------|--------|
| `polyphony/io/fetchers.py` | New — image download logic |
| `polyphony/generators.py` | New — template + LLM data generation |
| `polyphony/cli/cmd_data.py` | Modified — add `fetch-images` and `generate` commands |
| `tests/test_fetchers.py` | New — fetcher tests |
| `tests/test_generators.py` | New — generator tests |
