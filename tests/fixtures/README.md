This folder contains synthetic fixtures to exercise importers and exporters.

- `sample_documents_extended.json`: JSON array of interview documents (use with `import_documents(..., paths=[...])`).
- `sample_documents.csv`: CSV with a `content` column to test `read_csv` import path.
- `sample_llm_calls.jsonl`: Sample LLM call records (one JSON object per line) for `export_llm_log` testing.
- `sample_codebook.yaml`: Example codebook in YAML matching the exporter structure.
- `sample_assignments.csv`: Example assignments CSV matching the exporter output shape.

Usage examples (from project root):

```bash
# Import JSON into project DB (example project_id 1):
python -c "from polyphony.io.importers import import_documents; import sqlite3; conn=sqlite3.connect('polyphony.db'); print(import_documents(conn, 1, ['tests/fixtures/sample_documents_extended.json']))"

# Use CSV import path by pointing at the CSV file and using --content-col if needed
```
