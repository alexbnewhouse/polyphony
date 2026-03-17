"""
polyphony.io.importers
=================
Import qualitative data files into the polyphony database.

Supported formats:
  - .txt  : plain text
  - .md   : markdown (treated as plain text)
  - .csv  : CSV with a content column (configurable)
  - .json : JSON array of {content, metadata} objects
  - .docx : Microsoft Word documents (requires python-docx)
  - .png, .jpg, .jpeg, .gif, .webp, .bmp, .tiff, .tif : images

Segmentation strategies:
  - paragraph : split on blank lines (default, good for interview transcripts)
  - sentence  : split on sentence boundaries (requires simple regex)
  - fixed:<n> : fixed-length windows of n words
  - manual    : no splitting; each document = one segment

Image files are always treated as one segment per image (no text segmentation).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console

from ..db import fetchone, insert, json_col

console = Console()

IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
})


# ─────────────────────────────────────────────────────────────────────────────
# Hashing
# ─────────────────────────────────────────────────────────────────────────────


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Segmentation
# ─────────────────────────────────────────────────────────────────────────────


def segment_text(
    text: str,
    strategy: str = "paragraph",
    min_length: int = 20,
) -> List[Tuple[str, int, int]]:
    """
    Split text into segments according to strategy.
    Returns list of (segment_text, char_start, char_end).
    Skips segments shorter than min_length characters.
    """
    if strategy == "paragraph":
        return _split_paragraphs(text, min_length)
    elif strategy == "sentence":
        return _split_sentences(text, min_length)
    elif strategy == "manual":
        stripped = text.strip()
        if len(stripped) >= min_length:
            return [(stripped, 0, len(stripped))]
        return []
    elif strategy.startswith("fixed:"):
        try:
            n = int(strategy.split(":")[1])
            if n <= 0:
                raise ValueError("Window size must be positive")
        except (IndexError, ValueError) as e:
            raise ValueError(
                f"Invalid fixed-window strategy: '{strategy}'. Use 'fixed:100' (positive integer)."
            ) from e
        return _split_fixed(text, n, min_length)
    else:
        raise ValueError(
            f"Unknown segmentation strategy '{strategy}'. "
            "Choose: paragraph, sentence, fixed:<n>, manual."
        )


def _split_paragraphs(text: str, min_length: int) -> List[Tuple[str, int, int]]:
    """Split on one or more blank lines."""
    parts = re.split(r"\n\s*\n", text)
    result = []
    pos = 0
    for part in parts:
        part = part.strip()
        start = text.find(part, pos)
        end = start + len(part)
        if start >= 0 and len(part) >= min_length:
            result.append((part, start, end))
        pos = end
    return result


def _split_sentences(text: str, min_length: int) -> List[Tuple[str, int, int]]:
    """Split on sentence boundaries using simple regex."""
    # Group into chunks of ~3 sentences to avoid too-small segments
    sentence_end = re.compile(r"(?<=[.!?])\s+")
    sentences = sentence_end.split(text.strip())
    # Group into windows of 3
    chunks = []
    for i in range(0, len(sentences), 3):
        chunk = " ".join(sentences[i: i + 3]).strip()
        if len(chunk) >= min_length:
            chunks.append(chunk)

    result = []
    pos = 0
    for chunk in chunks:
        start = text.find(chunk, pos)
        if start >= 0:
            end = start + len(chunk)
            result.append((chunk, start, end))
            pos = end
        else:
            # chunk not found at expected position (e.g. whitespace mismatch);
            # advance pos to avoid searching from the beginning next time
            pos = min(pos + len(chunk), len(text))
    return result


def _split_fixed(text: str, n_words: int, min_length: int) -> List[Tuple[str, int, int]]:
    """Split into fixed-width word windows."""
    words = text.split()
    result = []
    char_pos = 0
    for i in range(0, len(words), n_words):
        chunk_words = words[i: i + n_words]
        chunk = " ".join(chunk_words)
        if len(chunk) < min_length:
            continue
        start = text.find(chunk, char_pos)
        end = start + len(chunk)
        result.append((chunk, start, end))
        char_pos = max(0, end - 10)  # small overlap for search
    return result


# ─────────────────────────────────────────────────────────────────────────────
# File readers
# ─────────────────────────────────────────────────────────────────────────────


def read_txt(path: Path) -> Tuple[str, dict]:
    return path.read_text(encoding="utf-8", errors="replace"), {}


def read_md(path: Path) -> Tuple[str, dict]:
    return path.read_text(encoding="utf-8", errors="replace"), {}


def read_docx(path: Path) -> Tuple[str, dict]:
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx is required for .docx files. Install: pip install python-docx")
    doc = Document(str(path))
    text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    metadata = {}
    if doc.core_properties.author:
        metadata["author"] = doc.core_properties.author
    return text, metadata


def read_csv(path: Path, content_col: str = "content") -> List[Tuple[str, dict]]:
    """Read CSV; returns list of (text, metadata) for each row."""
    results = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = row.get(content_col, "").strip()
            if not text:
                continue
            meta = {k: v for k, v in row.items() if k != content_col}
            results.append((text, meta))
    return results


def read_json(path: Path) -> List[Tuple[str, dict]]:
    """Read JSON array of {content, ...} objects."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    results = []
    for item in data:
        text = item.get("content") or item.get("text") or ""
        if not text:
            continue
        meta = {k: v for k, v in item.items() if k not in ("content", "text")}
        results.append((text, meta))
    return results


def read_image(path: Path, images_dir: Path) -> Tuple[str, str, str, dict]:
    """
    Read an image file and copy it into the project's images directory.

    Returns (placeholder_text, content_hash, stored_image_path, metadata).
    """
    raw_bytes = path.read_bytes()
    content_hash = sha256_bytes(raw_bytes)

    # Copy image to project images directory with hash prefix for uniqueness
    images_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{content_hash[:12]}_{path.name}"
    stored_path = images_dir / stored_name
    if not stored_path.exists():
        shutil.copy2(path, stored_path)

    placeholder = f"[IMAGE: {path.name}]"

    metadata: dict = {
        "original_filename": path.name,
        "file_size_bytes": len(raw_bytes),
        "image_format": path.suffix.lower().lstrip("."),
    }

    # Try to get image dimensions with Pillow (optional dependency)
    try:
        from PIL import Image
        with Image.open(path) as img:
            metadata["width"] = img.width
            metadata["height"] = img.height
    except ImportError:
        pass
    except Exception:
        pass

    return placeholder, content_hash, str(stored_path), metadata


# ─────────────────────────────────────────────────────────────────────────────
# Main import function
# ─────────────────────────────────────────────────────────────────────────────


def import_documents(
    conn: sqlite3.Connection,
    project_id: int,
    paths: List[Path],
    segment_strategy: str = "paragraph",
    content_col: str = "content",
    min_segment_length: int = 20,
    metadata_override: Optional[dict] = None,
    project_dir: Optional[Path] = None,
) -> dict:
    """
    Import one or more files into the project database.
    Returns a summary dict with counts.

    project_dir is required when importing images — images are copied into
    <project_dir>/images/ for persistence.
    """
    total_docs = 0
    total_segments = 0
    skipped = []
    images_dir = Path(project_dir) / "images" if project_dir else None

    for path in paths:
        path = Path(path)
        if not path.exists():
            console.print(f"[red]File not found: {path}[/]")
            skipped.append(str(path))
            continue

        suffix = path.suffix.lower()
        is_image = suffix in IMAGE_EXTENSIONS

        # Read file(s)
        entries: List[Tuple[str, dict]] = []
        image_info: Optional[Tuple[str, str, str, dict]] = None  # for images only
        try:
            if is_image:
                if images_dir is None:
                    console.print(
                        f"[red]Cannot import image {path.name}: project directory unknown. "
                        f"Use `polyphony data import` from within a project.[/]"
                    )
                    skipped.append(str(path))
                    continue
                placeholder, content_hash, stored_path, meta = read_image(path, images_dir)
                image_info = (placeholder, content_hash, stored_path, meta)
                entries = [(placeholder, meta)]
            elif suffix in (".txt", ".md"):
                text, meta = read_txt(path) if suffix == ".txt" else read_md(path)
                entries = [(text, meta)]
            elif suffix == ".docx":
                text, meta = read_docx(path)
                entries = [(text, meta)]
            elif suffix == ".csv":
                entries = read_csv(path, content_col)
            elif suffix == ".json":
                entries = read_json(path)
            else:
                console.print(f"[yellow]Unsupported file type: {suffix} ({path.name})[/]")
                skipped.append(str(path))
                continue
        except Exception as e:
            console.print(f"[red]Error reading {path.name}: {e}[/]")
            skipped.append(str(path))
            continue

        for i, (text, meta) in enumerate(entries):
            if not text.strip():
                continue

            filename = path.name if len(entries) == 1 else f"{path.stem}_row{i + 1}{path.suffix}"

            if is_image and image_info:
                content_hash = image_info[1]
            else:
                content_hash = sha256(text)

            # Check for duplicates
            existing = fetchone(
                conn,
                "SELECT id FROM document WHERE project_id = ? AND content_hash = ?",
                (project_id, content_hash),
            )
            if existing:
                console.print(f"[dim]Skipping duplicate: {filename}[/]")
                continue

            if metadata_override:
                meta.update(metadata_override)

            # Insert document
            doc_data = {
                "project_id": project_id,
                "filename": filename,
                "source_path": str(path),
                "content": text,
                "content_hash": content_hash,
                "char_count": len(text),
                "word_count": len(text.split()),
                "metadata": json_col(meta),
                "media_type": "image" if is_image else "text",
            }
            if is_image and image_info:
                doc_data["image_path"] = image_info[2]
            doc_id = insert(conn, "document", doc_data)

            if is_image and image_info:
                # Image documents: exactly 1 segment per image, no text segmentation
                insert(conn, "segment", {
                    "document_id": doc_id,
                    "project_id": project_id,
                    "segment_index": 0,
                    "text": text,
                    "char_start": 0,
                    "char_end": len(text),
                    "segment_hash": content_hash,
                    "is_calibration": 0,
                    "media_type": "image",
                    "image_path": image_info[2],
                })
                total_segments += 1
            else:
                # Text documents: segment as usual
                segments_data = segment_text(text, segment_strategy, min_segment_length)
                if not segments_data:
                    console.print(
                        f"  [yellow]Warning: {filename}: no segments passed the minimum length "
                        f"({min_segment_length} chars). Try --min-length 10 or --segment-by manual.[/]"
                    )
                    conn.execute("DELETE FROM document WHERE id = ?", (doc_id,))
                    continue

                for seg_idx, (seg_text, char_start, char_end) in enumerate(segments_data):
                    insert(conn, "segment", {
                        "document_id": doc_id,
                        "project_id": project_id,
                        "segment_index": seg_idx,
                        "text": seg_text,
                        "char_start": char_start,
                        "char_end": char_end,
                        "segment_hash": sha256(seg_text),
                        "is_calibration": 0,
                    })
                    total_segments += 1

            # Update document status
            conn.execute(
                "UPDATE document SET status = 'segmented' WHERE id = ?", (doc_id,)
            )
            total_docs += 1

        conn.commit()
        console.print(
            f"  [green]✓[/] {path.name}: {len(entries)} document(s), "
            f"{total_segments} segments so far."
        )

    # Advance project status if this is the first import
    project = fetchone(conn, "SELECT status FROM project WHERE id = ?", (project_id,))
    if project and project["status"] == "setup":
        conn.execute(
            "UPDATE project SET status = 'inducing', updated_at = datetime('now') WHERE id = ?",
            (project_id,),
        )
        conn.commit()

    return {
        "documents_imported": total_docs,
        "segments_created": total_segments,
        "skipped": skipped,
    }
