"""Tests for data import and segmentation."""

import json
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from polyphony.io.importers import (
    IMAGE_EXTENSIONS,
    import_documents,
    segment_text,
    sha256,
    sha256_bytes,
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


# ─────────────────────────────────────────────────────────────────────────────
# Image imports
# ─────────────────────────────────────────────────────────────────────────────


def _make_minimal_png(path: Path) -> None:
    """Create a valid 1x1 red pixel PNG file (no Pillow needed)."""
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    # IHDR: 1x1, 8-bit RGB
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    # IDAT: single red pixel (filter byte 0 + RGB)
    raw = b"\x00\xff\x00\x00"
    idat = zlib.compress(raw)
    # IEND
    png_data = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(png_data)


def _make_minimal_jpeg(path: Path) -> None:
    """Create a minimal valid JPEG file (smallest valid JFIF)."""
    # Minimal JPEG: SOI + APP0 + DQT + SOF0 + DHT + SOS + EOI
    # Simpler: just write a recognizable JPEG header and some data
    path.write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xd9"
    )


def test_import_image_png(conn, project_id, tmp_path):
    """PNG images should import as 1 document + 1 segment with media_type='image'."""
    png = tmp_path / "photo.png"
    _make_minimal_png(png)

    result = import_documents(
        conn, project_id, [png],
        project_dir=tmp_path,
    )
    assert result["documents_imported"] == 1
    assert result["segments_created"] == 1

    from polyphony.db import fetchone
    doc = fetchone(conn, "SELECT * FROM document WHERE project_id = ? AND media_type = 'image'", (project_id,))
    assert doc is not None
    assert doc["media_type"] == "image"
    assert doc["image_path"] is not None
    assert Path(doc["image_path"]).exists()

    seg = fetchone(conn, "SELECT * FROM segment WHERE document_id = ?", (doc["id"],))
    assert seg is not None
    assert seg["media_type"] == "image"
    assert seg["image_path"] is not None
    assert seg["segment_index"] == 0


def test_import_image_jpeg(conn, project_id, tmp_path):
    """JPEG images should import correctly."""
    jpg = tmp_path / "photo.jpg"
    _make_minimal_jpeg(jpg)

    result = import_documents(
        conn, project_id, [jpg],
        project_dir=tmp_path,
    )
    assert result["documents_imported"] == 1
    assert result["segments_created"] == 1


def test_import_mixed_text_and_images(conn, project_id, tmp_path):
    """Mixed imports of text and images should work correctly."""
    txt = tmp_path / "interview.txt"
    txt.write_text(
        "First paragraph about housing.\n\nSecond paragraph about rent increases.",
        encoding="utf-8",
    )
    png = tmp_path / "evidence.png"
    _make_minimal_png(png)

    result = import_documents(
        conn, project_id, [txt, png],
        segment_strategy="paragraph",
        project_dir=tmp_path,
    )
    assert result["documents_imported"] == 2
    # 2 text segments from paragraphs + 1 image segment
    assert result["segments_created"] == 3


def test_image_single_segment(conn, project_id, tmp_path):
    """Image documents should always produce exactly 1 segment per image."""
    png = tmp_path / "single.png"
    _make_minimal_png(png)
    result = import_documents(conn, project_id, [png], project_dir=tmp_path)
    assert result["documents_imported"] == 1
    assert result["segments_created"] == 1

    from polyphony.db import fetchall
    segs = fetchall(
        conn,
        "SELECT * FROM segment WHERE project_id = ? AND media_type = 'image'",
        (project_id,),
    )
    # Each image segment should have segment_index 0
    for seg in segs:
        assert seg["segment_index"] == 0


def test_image_deduplication(conn, project_id, tmp_path):
    """Same image imported twice should be deduplicated."""
    png = tmp_path / "photo.png"
    _make_minimal_png(png)

    result1 = import_documents(conn, project_id, [png], project_dir=tmp_path)
    result2 = import_documents(conn, project_id, [png], project_dir=tmp_path)
    assert result1["documents_imported"] == 1
    assert result2["documents_imported"] == 0  # duplicate skipped


def test_sha256_bytes_deterministic():
    data = b"hello world"
    assert sha256_bytes(data) == sha256_bytes(data)


def test_sha256_bytes_different():
    assert sha256_bytes(b"hello") != sha256_bytes(b"world")


def test_image_requires_project_dir(conn, project_id, tmp_path):
    """Image imports without project_dir should be skipped."""
    png = tmp_path / "photo.png"
    _make_minimal_png(png)

    result = import_documents(
        conn, project_id, [png],
        project_dir=None,
    )
    assert result["documents_imported"] == 0
    assert len(result["skipped"]) == 1
