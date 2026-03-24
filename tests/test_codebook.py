"""Tests for codebook management."""

import csv
import json
from pathlib import Path

import pytest
import yaml

from polyphony.db import fetchall, fetchone
from polyphony.pipeline.induction import (
    merge_candidates,
    save_codebook_version,
    select_induction_sample,
)
from polyphony.prompts import format_codebook


# ─────────────────────────────────────────────────────────────────────────────
# Induction helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_merge_candidates_deduplication():
    candidates_a = [
        {"name": "FINANCIAL_STRESS", "description": "Financial worry", "level": "open"},
        {"name": "ISOLATION", "description": "Social isolation", "level": "open"},
    ]
    candidates_b = [
        {"name": "FINANCIAL_STRESS", "description": "Money anxiety", "level": "open"},
        {"name": "SHAME", "description": "Embarrassment", "level": "open"},
    ]
    merged = merge_candidates(candidates_a, candidates_b)
    names = [c["name"] for c in merged]
    # FINANCIAL_STRESS should appear only once
    assert names.count("FINANCIAL_STRESS") == 1
    assert len(merged) == 3  # FINANCIAL_STRESS, ISOLATION, SHAME


def test_merge_candidates_enriches_description():
    a = [{"name": "CODE_A", "description": "First description", "level": "open"}]
    b = [{"name": "CODE_A", "description": "Second description", "level": "open"}]
    merged = merge_candidates(a, b)
    assert len(merged) == 1
    # Both descriptions should be present
    assert "First description" in merged[0]["description"]
    assert "Second description" in merged[0]["description"]


def test_merge_candidates_empty():
    assert merge_candidates([], []) == []
    result = merge_candidates([{"name": "CODE_A", "description": "X", "level": "open"}], [])
    assert len(result) == 1


def test_select_induction_sample(conn, project_id, document_id):
    sample = select_induction_sample(conn, project_id, n=5, seed=42)
    assert len(sample) <= 5
    assert all("text" in s for s in sample)
    assert all("segment_index" in s for s in sample)


def test_select_induction_sample_stratified(conn, project_id, document_id):
    """Sample should include segments from the document."""
    sample = select_induction_sample(conn, project_id, n=10, seed=42)
    doc_ids = {s["document_id"] for s in sample}
    assert len(doc_ids) >= 1


def test_select_induction_sample_no_segments(conn, project_id):
    """Should raise ValueError when no segments exist."""
    with pytest.raises(ValueError, match="No segments"):
        select_induction_sample(conn, project_id, n=5)


def test_save_codebook_version(conn, project_id):
    codes = [
        {"name": "CODE_A", "description": "Test code A", "level": "open",
         "inclusion_criteria": "Include when...", "exclusion_criteria": "",
         "example_quotes": []},
    ]
    cb_id = save_codebook_version(conn, project_id, codes, version=1)
    assert cb_id > 0

    cb = fetchone(conn, "SELECT * FROM codebook_version WHERE id = ?", (cb_id,))
    assert cb["version"] == 1
    assert cb["stage"] == "draft"

    saved_codes = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ?", (cb_id,))
    assert len(saved_codes) == 1
    assert saved_codes[0]["name"] == "CODE_A"


# ─────────────────────────────────────────────────────────────────────────────
# Codebook formatting
# ─────────────────────────────────────────────────────────────────────────────


def test_format_codebook_empty():
    result = format_codebook([])
    assert "No codes" in result


def test_format_codebook_basic():
    codes = [
        {
            "name": "FINANCIAL_STRESS",
            "description": "Worry about finances",
            "level": "open",
            "inclusion_criteria": "When money is mentioned",
            "exclusion_criteria": "",
            "example_quotes": json.dumps(["I can't pay my bills"]),
            "is_active": 1,
            "sort_order": 0,
        }
    ]
    result = format_codebook(codes)
    assert "FINANCIAL_STRESS" in result
    assert "Worry about finances" in result
    assert "When money is mentioned" in result
    assert "OPEN CODES" in result


def test_format_codebook_groups_by_level():
    codes = [
        {"name": "CODE_OPEN", "description": "Open", "level": "open",
         "inclusion_criteria": None, "exclusion_criteria": None,
         "example_quotes": "[]", "is_active": 1, "sort_order": 0},
        {"name": "CODE_AXIAL", "description": "Axial", "level": "axial",
         "inclusion_criteria": None, "exclusion_criteria": None,
         "example_quotes": "[]", "is_active": 1, "sort_order": 0},
    ]
    result = format_codebook(codes)
    assert "OPEN CODES" in result
    assert "AXIAL CODES" in result
    assert result.index("OPEN CODES") < result.index("AXIAL CODES")


# ─────────────────────────────────────────────────────────────────────────────
# Codebook import (deductive workflow)
# ─────────────────────────────────────────────────────────────────────────────

IMPORT_CODES = [
    {
        "name": "POPULIST_RHETORIC",
        "description": "Speaker uses populist framing",
        "level": "open",
        "inclusion_criteria": "Us vs them language, anti-elite sentiment",
        "exclusion_criteria": "Policy disagreement without populist framing",
    },
    {
        "name": "ECONOMIC_ANXIETY",
        "description": "Speaker expresses concern about economic conditions",
        "level": "open",
        "inclusion_criteria": "References to job loss, inflation, economic decline",
        "exclusion_criteria": "Abstract economic theory without personal concern",
    },
    {
        "name": "INSTITUTIONAL_DISTRUST",
        "description": "Speaker expresses distrust of institutions",
        "level": "axial",
        "inclusion_criteria": "Distrust of government, media, or corporations",
        "exclusion_criteria": "Specific policy criticism without broader distrust",
    },
]


def _parse_codebook_from_yaml(file_path):
    """Helper: parse a YAML codebook file the same way the CLI does."""
    data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    codes = data.get("codes", data) if isinstance(data, dict) else data
    return codes


def _parse_codebook_from_json(file_path):
    """Helper: parse a JSON codebook file the same way the CLI does."""
    data = json.loads(file_path.read_text(encoding="utf-8"))
    codes = data.get("codes", data) if isinstance(data, dict) else data
    return codes


def _parse_codebook_from_csv(file_path):
    """Helper: parse a CSV codebook file the same way the CLI does."""
    with file_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def test_import_codebook_from_yaml(tmp_path, conn, project_id):
    """Import a codebook from a YAML file and verify codes are saved."""
    codebook_file = tmp_path / "codebook.yaml"
    codebook_file.write_text(
        yaml.dump({"codes": IMPORT_CODES}, allow_unicode=True, sort_keys=False)
    )

    codes = _parse_codebook_from_yaml(codebook_file)
    assert len(codes) == 3

    cb_id = save_codebook_version(
        conn, project_id, codes, version=1,
        stage="imported", rationale="Imported from codebook.yaml",
    )

    saved = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ? ORDER BY sort_order", (cb_id,))
    assert len(saved) == 3
    assert saved[0]["name"] == "POPULIST_RHETORIC"
    assert saved[0]["description"] == "Speaker uses populist framing"
    assert saved[0]["inclusion_criteria"] == "Us vs them language, anti-elite sentiment"
    assert saved[1]["name"] == "ECONOMIC_ANXIETY"
    assert saved[2]["name"] == "INSTITUTIONAL_DISTRUST"
    assert saved[2]["level"] == "axial"

    cb = fetchone(conn, "SELECT * FROM codebook_version WHERE id = ?", (cb_id,))
    assert cb["stage"] == "imported"
    assert "codebook.yaml" in cb["rationale"]


def test_import_codebook_from_json(tmp_path, conn, project_id):
    """Import a codebook from a JSON file and verify codes are saved."""
    codebook_file = tmp_path / "codebook.json"
    codebook_file.write_text(
        json.dumps({"codes": IMPORT_CODES}, indent=2, ensure_ascii=False)
    )

    codes = _parse_codebook_from_json(codebook_file)
    assert len(codes) == 3

    cb_id = save_codebook_version(
        conn, project_id, codes, version=1,
        stage="imported", rationale="Imported from codebook.json",
    )

    saved = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ? ORDER BY sort_order", (cb_id,))
    assert len(saved) == 3
    assert saved[0]["name"] == "POPULIST_RHETORIC"
    assert saved[1]["name"] == "ECONOMIC_ANXIETY"
    assert saved[2]["name"] == "INSTITUTIONAL_DISTRUST"


def test_import_codebook_from_csv(tmp_path, conn, project_id):
    """Import a codebook from a CSV file and verify codes are saved."""
    codebook_file = tmp_path / "codebook.csv"
    fieldnames = ["name", "description", "inclusion_criteria", "exclusion_criteria", "level"]
    with codebook_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for code in IMPORT_CODES:
            writer.writerow({k: code.get(k, "") for k in fieldnames})

    codes = _parse_codebook_from_csv(codebook_file)
    assert len(codes) == 3

    cb_id = save_codebook_version(
        conn, project_id, codes, version=1,
        stage="imported", rationale="Imported from codebook.csv",
    )

    saved = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ? ORDER BY sort_order", (cb_id,))
    assert len(saved) == 3
    assert saved[0]["name"] == "POPULIST_RHETORIC"
    assert saved[1]["description"] == "Speaker expresses concern about economic conditions"
    assert saved[2]["level"] == "axial"


def test_import_codebook_values_match_expected(tmp_path, conn, project_id):
    """Verify that all imported code fields round-trip correctly."""
    codebook_file = tmp_path / "codebook.yaml"
    codebook_file.write_text(
        yaml.dump({"codes": IMPORT_CODES}, allow_unicode=True, sort_keys=False)
    )

    codes = _parse_codebook_from_yaml(codebook_file)
    cb_id = save_codebook_version(
        conn, project_id, codes, version=1,
        stage="imported", rationale="Round-trip test",
    )

    saved = fetchall(conn, "SELECT * FROM code WHERE codebook_version_id = ? ORDER BY sort_order", (cb_id,))
    for original, saved_code in zip(IMPORT_CODES, saved):
        assert saved_code["name"] == original["name"]
        assert saved_code["description"] == original["description"]
        assert saved_code["level"] == original["level"]
        assert saved_code["inclusion_criteria"] == original["inclusion_criteria"]
        assert saved_code["exclusion_criteria"] == original["exclusion_criteria"]
        assert saved_code["is_active"] == 1


def test_deductive_coding_prompt_template_loads():
    """Verify the deductive_coding prompt template loads and renders."""
    from polyphony.prompts import PromptLibrary
    lib = PromptLibrary()
    tmpl = lib["deductive_coding"]
    assert tmpl.name == "deductive_coding"

    system, user = tmpl.render(
        methodology="content_analysis",
        research_question="How is populism framed?",
        codebook_version=1,
        codebook_formatted="POPULIST_RHETORIC: ...",
        document_filename="test.txt",
        segment_index=1,
        total_segments=10,
        segment_text="The elites don't care about us.",
    )
    # Deductive prompt should emphasize fixed codebook
    assert "FIXED" in system or "FIXED" in user
    assert "Do NOT" in system
    assert "missing_code" not in system or "Do NOT use" in system
