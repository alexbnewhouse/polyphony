# Image URL Fetcher & Fake Data Generator — Design Spec

**Date:** 2026-03-23
**Updated:** 2026-04-08
**Status:** Implemented

## Overview

Two capabilities for polyphony:

1. **CSV Image URL Fetcher** — Download images from URLs listed in a CSV and import them as multimodal documents. Supports both direct image URLs and inferential page scraping via `--scraper`.
2. **Fake Data Generator** — Generate realistic synthetic QDA datasets for training and practice

## Feature 1: CSV Image URL Fetcher

### CLI Interface

```bash
# Direct image URL download
polyphony data fetch-images urls.csv --url-column "image_url" --metadata-columns "caption,source"

# Inferential scraper: URLs are web pages, not direct image links
polyphony data fetch-images threads.csv --url-column "4PLEBS POST" --scraper 4plebs
polyphony data fetch-images pages.csv --url-column page_url --scraper generic
```

**Arguments:**
- `csv_path` (required): Path to CSV file containing image URLs or page URLs
- `--url-column` (default: `"url"`): Column name containing image or page URLs
- `--metadata-columns`: Comma-separated list of columns to preserve as document metadata
- `--timeout` (default: 30): Per-image download timeout in seconds
- `--max-concurrent` (default: 5): Maximum concurrent downloads
- `--scraper` (optional, choices: `4plebs`, `generic`): Treat each URL as a web page and extract images from it before downloading

### Scraper Modes

When `--scraper` is set, `_scrape_one_page()` fetches each page URL, parses the HTML, extracts image URLs via the chosen extractor, and downloads each one. If the URL responds with `image/*` content (i.e. it was already a direct link), the scraper falls back to a direct download automatically.

| Scraper | Extractor function | Behaviour |
|---|---|---|
| `4plebs` | `extract_4plebs_images` | Targets `i.4pcdn.org` hostnames; skips thumbnails (filenames matching `<digits>s.<ext>`) |
| `generic` | `extract_html_images` | Finds all `<a href>` and `<img src>` pointing to recognised image extensions |

Both extractors: deduplicate URLs, resolve relative URLs, and reject non-http(s) schemes.

### Architecture

**`polyphony/io/fetchers.py`**

- `_HTMLImageParser` — stdlib `html.parser` subclass collecting `<a href>` and `<img src>` with relative URL resolution
- `extract_html_images(page_url, html)` — generic image URL extractor
- `extract_4plebs_images(page_url, html)` — 4plebs/4chan archive extractor
- `PAGE_EXTRACTORS` — `Dict[str, Callable]` registry (`{"4plebs": ..., "generic": ...}`)
- `_scrape_one_page(page_url, extractor, images_dir, metadata, timeout)` — fetches page, extracts, downloads
- `_download_one(url, images_dir, metadata, timeout)` — downloads a single direct image URL
- `fetch_images_from_csv(...)` — orchestrates the full workflow; accepts optional `page_image_extractor`

**`polyphony/cli/cmd_data.py`**

`fetch-images` command gains `--scraper` (Click `Choice` from `PAGE_EXTRACTORS` keys). Passes resolved extractor as `page_image_extractor` to `fetch_images_from_csv`.

### Data Flow

```
CSV file → parse URLs → [optional: fetch page HTML → extract image URLs]
         → download images (concurrent) → save to images/ → import_image() → DB
```

### Error Handling

- Invalid URLs: skip with warning
- Download failures: 1 retry, then fail with error
- Duplicate images: SHA256-based deduplication
- Non-image content-type (direct mode): fail with informative error
- Page with no images found (scraper mode): fail entry with "No images found on page"
- Network timeout: configurable, default 30s
- Private/internal IP SSRF protection: blocked at both page-fetch and image-download level

### Constraints

- No new dependencies — uses only stdlib (`urllib.request`, `html.parser`, `concurrent.futures`)
- Reuses existing `import_image()` / `import_documents()` for DB insertion

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
