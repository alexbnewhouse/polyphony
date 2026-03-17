"""Tests for data import and segmentation."""

import json
import tempfile
from pathlib import Path

import pytest

from polyphony.io.importers import (
    import_documents,
    segment_text,
    sha256,
)


# ─────────────────────────────────────────────────────────────────────────────
# Segmentation
# ─────────────────────────────────────────────────────────────────────────────


def test_segment_paragraph():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    segs = segment_text(text, "paragraph", min_length=5)
    assert len(segs) == 3
    assert segs[0][0] == "First paragraph."


def test_segment_fixed():
    words = " ".join(f"word{i}" for i in range(60))
    segs = segment_text(words, "fixed:20", min_length=10)
    assert len(segs) == 3  # 60 words / 20 = 3 segments
    for s, start, end in segs:
        assert len(s.split()) == 20


def test_segment_manual():
    text = "This is the whole document as one segment."
    segs = segment_text(text, "manual", min_length=5)
    assert len(segs) == 1
    assert segs[0][0] == text.strip()


def test_segment_min_length_filters():
    text = "Hi.\n\nThis is a longer paragraph with enough content."
    segs = segment_text(text, "paragraph", min_length=20)
    assert len(segs) == 1  # "Hi." is too short
    assert "longer paragraph" in segs[0][0]


def test_segment_unknown_strategy():
    with pytest.raises(ValueError, match="Unknown"):
        segment_text("text", "unknown_strategy")


def test_segment_fixed_bad_format():
    with pytest.raises(ValueError, match="fixed"):
        segment_text("text", "fixed:notanumber")


# ─────────────────────────────────────────────────────────────────────────────
# sha256
# ─────────────────────────────────────────────────────────────────────────────


def test_sha256_deterministic():
    text = "hello world"
    assert sha256(text) == sha256(text)


def test_sha256_different():
    assert sha256("hello") != sha256("world")


# ─────────────────────────────────────────────────────────────────────────────
# import_documents
# ─────────────────────────────────────────────────────────────────────────────


def test_import_txt(conn, project_id, tmp_path):
    txt = tmp_path / "interview.txt"
    txt.write_text(
        "First response about housing and finances.\n\nSecond response is longer than minimum length.\n\nThird response about social isolation.",
        encoding="utf-8",
    )
    result = import_documents(conn, project_id, [txt], segment_strategy="paragraph")
    assert result["documents_imported"] == 1
    assert result["segments_created"] >= 2


def test_import_csv(conn, project_id, tmp_path):
    csv_file = tmp_path / "survey.csv"
    csv_file.write_text(
        "content,participant_id\n"
        "\"This is a long enough response about housing.\",P001\n"
        "\"Another participant response about finances and stress.\",P002\n",
        encoding="utf-8",
    )
    result = import_documents(conn, project_id, [csv_file], content_col="content")
    assert result["documents_imported"] == 2


def test_import_json(conn, project_id, tmp_path):
    json_file = tmp_path / "data.json"
    data = [
        {"content": "Interview excerpt about housing insecurity and rent.", "id": 1},
        {"content": "Second interview about food poverty and coping strategies.", "id": 2},
    ]
    json_file.write_text(json.dumps(data))
    result = import_documents(conn, project_id, [json_file])
    assert result["documents_imported"] == 2


def test_import_deduplication(conn, project_id, tmp_path):
    txt = tmp_path / "interview.txt"
    content = "First paragraph.\n\nSecond paragraph with enough text here."
    txt.write_text(content)
    result1 = import_documents(conn, project_id, [txt])
    result2 = import_documents(conn, project_id, [txt])  # second import
    assert result1["documents_imported"] == 1
    assert result2["documents_imported"] == 0  # duplicate skipped


def test_import_missing_file(conn, project_id):
    result = import_documents(conn, project_id, [Path("/nonexistent/file.txt")])
    assert result["documents_imported"] == 0
    assert len(result["skipped"]) == 1
