"""
polyphony.io.fetchers
=====================
Download images from URLs listed in a CSV file.

Fetches images concurrently, deduplicates by SHA256 hash, and saves
them locally for subsequent import into a polyphony project.
"""

from __future__ import annotations

import csv
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn

from .importers import sha256_bytes
from .net_safety import SafeRedirectHandler as _BaseSafeRedirectHandler, is_safe_host

# Maximum download size per image (50 MB)
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _sanitize_filename(url: str) -> str:
    """Extract a safe filename from a URL (no path traversal)."""
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name or name == "/":
        name = "image"
    # Strip query params and any path components
    name = name.split("?")[0]
    # Remove any remaining path separators to prevent traversal
    name = re.sub(r'[/\\]', '_', name)
    # Keep enough room for hash prefixes and common filesystem limits.
    return name[:240]


def _is_safe_host(hostname: str) -> bool:
    """Compatibility wrapper for legacy tests and call sites."""
    return is_safe_host(hostname)


class SafeRedirectHandler(_BaseSafeRedirectHandler):
    """Compatibility wrapper that consults fetchers._is_safe_host for tests."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise urllib.error.URLError(f"Redirected to unsupported scheme: {newurl}")
        if not _is_safe_host(parsed.hostname or ""):
            raise urllib.error.URLError(f"Redirected to unsafe host: {newurl}")
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl,
        )

def _download_one(
    url: str,
    images_dir: Path,
    metadata: dict,
    timeout: int,
) -> dict:
    """
    Download a single image URL.

    Returns a result dict with status "downloaded", "skipped", or "failed".
    Retries once on failure.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {
            "status": "failed",
            "url": url,
            "metadata": metadata,
            "error": f"Unsupported scheme: {parsed.scheme!r} (only http/https allowed)",
        }

    if not _is_safe_host(parsed.hostname or ""):
        return {
            "status": "failed",
            "url": url,
            "metadata": metadata,
            "error": "URL points to a private/internal address (blocked for security)",
        }

    opener = urllib.request.build_opener(SafeRedirectHandler())
    filename = _sanitize_filename(url)
    last_error: Optional[str] = None

    for attempt in range(2):  # 1 retry
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "polyphony-fetcher/1.0"})
            with opener.open(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    return {
                        "status": "failed",
                        "url": url,
                        "metadata": metadata,
                        "error": f"Invalid content type: {content_type} (not an image)",
                    }
                data = resp.read(_MAX_DOWNLOAD_BYTES + 1)
                if len(data) > _MAX_DOWNLOAD_BYTES:
                    return {
                        "status": "failed",
                        "url": url,
                        "metadata": metadata,
                        "error": f"File exceeds maximum size ({_MAX_DOWNLOAD_BYTES // (1024*1024)} MB)",
                    }
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt == 0:
                continue
            return {
                "status": "failed",
                "url": url,
                "metadata": metadata,
                "error": last_error,
            }

    # Deduplicate via SHA256 hash prefix (matches importers.py pattern)
    content_hash = sha256_bytes(data)
    stored_name = f"{content_hash[:12]}_{filename}"
    stored_path = images_dir / stored_name

    if stored_path.exists():
        return {
            "status": "skipped",
            "url": url,
            "path": stored_path,
            "metadata": metadata,
            "reason": "duplicate (file already exists)",
        }

    stored_path.write_bytes(data)

    return {
        "status": "downloaded",
        "url": url,
        "path": stored_path,
        "metadata": metadata,
    }


def fetch_images_from_csv(
    csv_path: Path,
    images_dir: Path,
    url_column: str = "url",
    metadata_columns: Optional[List[str]] = None,
    timeout: int = 30,
    max_concurrent: int = 5,
) -> dict:
    """
    Download images from URLs in a CSV and save locally.

    Returns {"downloaded": [...], "skipped": [...], "failed": [...]}.
    Each downloaded entry: {"path": Path, "url": str, "metadata": dict}
    """
    csv_path = Path(csv_path)
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    # Parse CSV
    rows: List[dict] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return {"downloaded": [], "skipped": [], "failed": []}
        if url_column not in reader.fieldnames:
            raise ValueError(
                f"URL column {url_column!r} not found in CSV. "
                f"Available columns: {', '.join(reader.fieldnames)}"
            )
        for row in reader:
            url = row.get(url_column, "").strip()
            if not url:
                continue
            meta = {}
            if metadata_columns:
                for col in metadata_columns:
                    if col in row:
                        meta[col] = row[col]
            else:
                # Include all non-URL columns as metadata
                meta = {k: v for k, v in row.items() if k != url_column}
            rows.append({"url": url, "metadata": meta})

    downloaded: List[dict] = []
    skipped: List[dict] = []
    failed: List[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[status]}[/]"),
    ) as progress:
        task = progress.add_task(
            "Fetching images", total=len(rows), status=""
        )

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            future_to_row = {
                executor.submit(
                    _download_one,
                    row["url"],
                    images_dir,
                    row["metadata"],
                    timeout,
                ): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                result = future.result()
                status = result["status"]

                if status == "downloaded":
                    downloaded.append(result)
                    progress.update(task, advance=1, status=f"[green]OK[/] {_sanitize_filename(result['url'])}")
                elif status == "skipped":
                    skipped.append(result)
                    progress.update(task, advance=1, status=f"[yellow]skip[/] {_sanitize_filename(result['url'])}")
                else:
                    failed.append(result)
                    progress.update(task, advance=1, status=f"[red]fail[/] {result.get('error', '')[:50]}")

    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
    }
