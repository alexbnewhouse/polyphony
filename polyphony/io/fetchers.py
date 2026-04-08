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
import ssl
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse, urljoin

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn

from .importers import sha256_bytes
from .net_safety import SafeRedirectHandler as _BaseSafeRedirectHandler, is_safe_host

# Maximum download size per image (50 MB)
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
# Maximum page size for HTML scraping (5 MB)
_MAX_PAGE_BYTES = 5 * 1024 * 1024


def _make_ssl_context() -> ssl.SSLContext:
    """Return an SSL context with verified certificates.

    Prefers certifi's CA bundle (required on macOS where Python does not use
    the system keychain).  Falls back to the default context if certifi is not
    installed.
    """
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    return ctx

_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".avif",
})


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
    """Thin alias of net_safety.SafeRedirectHandler for use in this module."""


class _HTMLImageParser(HTMLParser):
    """Collect href and src attributes from HTML for image URL discovery."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.hrefs: List[str] = []
        self.img_srcs: List[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:  # noqa: N802
        attr_dict = dict(attrs)
        if tag == "a":
            href = attr_dict.get("href", "")
            if href:
                self.hrefs.append(urljoin(self.base_url, href))
        elif tag == "img":
            src = attr_dict.get("src", "")
            if src:
                self.img_srcs.append(urljoin(self.base_url, src))


def extract_html_images(page_url: str, html: str) -> List[str]:
    """Generic extractor: return image URLs found in <a href> and <img src> tags."""
    parser = _HTMLImageParser(page_url)
    parser.feed(html)
    seen: set = set()
    result: List[str] = []
    for url in parser.hrefs + parser.img_srcs:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if Path(parsed.path).suffix.lower() in _IMAGE_EXTENSIONS:
            if url not in seen:
                seen.add(url)
                result.append(url)
    return result


def extract_4plebs_images(page_url: str, html: str) -> List[str]:
    """Extract full-size image URLs from a 4plebs/4chan archive thread page.

    Targets ``i.4pcdn.org`` links in ``<a href>`` attributes and skips
    thumbnails, which are named ``<digits>s.<ext>`` (e.g. ``1636483147264s.jpg``).
    """
    parser = _HTMLImageParser(page_url)
    parser.feed(html)
    seen: set = set()
    result: List[str] = []
    for url in parser.hrefs + parser.img_srcs:
        parsed = urlparse(url)
        if parsed.hostname != "i.4pcdn.org":
            continue
        stem = Path(parsed.path).stem
        # Skip thumbnails: stem ends with 's' and the rest is a Unix timestamp
        if stem.endswith("s") and stem[:-1].isdigit():
            continue
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


# Registry of built-in page image extractors.
PAGE_EXTRACTORS: Dict[str, Callable[[str, str], List[str]]] = {
    "4plebs": extract_4plebs_images,
    "generic": extract_html_images,
}


def _scrape_one_page(
    page_url: str,
    extractor: Callable[[str, str], List[str]],
    images_dir: Path,
    metadata: dict,
    timeout: int,
) -> List[dict]:
    """Fetch an HTML page, extract image URLs via *extractor*, download each.

    Returns a list of result dicts with the same schema as ``_download_one``.
    Falls back to a direct download if the URL resolves to an image.
    """
    parsed = urlparse(page_url)
    if parsed.scheme not in ("http", "https"):
        return [{"status": "failed", "url": page_url, "metadata": metadata,
                 "error": f"Unsupported scheme: {parsed.scheme!r} (only http/https allowed)"}]

    if not _is_safe_host(parsed.hostname or ""):
        return [{"status": "failed", "url": page_url, "metadata": metadata,
                 "error": "Page URL points to a private/internal address (blocked for security)"}]

    opener = urllib.request.build_opener(
        SafeRedirectHandler(),
        urllib.request.HTTPSHandler(context=_make_ssl_context()),
    )
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": "polyphony-fetcher/1.0"})
        with opener.open(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if content_type.startswith("image/"):
                # URL is a direct image; delegate to the standard download path.
                return [_download_one(page_url, images_dir, metadata, timeout)]
            charset_match = re.search(r"charset=([^\s;]+)", content_type)
            charset = charset_match.group(1) if charset_match else "utf-8"
            raw = resp.read(_MAX_PAGE_BYTES)
        html = raw.decode(charset, errors="replace")
    except Exception as exc:
        return [{"status": "failed", "url": page_url, "metadata": metadata, "error": str(exc)}]

    image_urls = extractor(page_url, html)
    if not image_urls:
        return [{"status": "failed", "url": page_url, "metadata": metadata,
                 "error": "No images found on page"}]

    page_meta = {**metadata, "page_url": page_url}
    return [_download_one(img_url, images_dir, page_meta, timeout) for img_url in image_urls]


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

    opener = urllib.request.build_opener(
        SafeRedirectHandler(),
        urllib.request.HTTPSHandler(context=_make_ssl_context()),
    )
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
    page_image_extractor: Optional[Callable[[str, str], List[str]]] = None,
) -> dict:
    """
    Download images from URLs in a CSV and save locally.

    When *page_image_extractor* is provided (e.g. ``PAGE_EXTRACTORS["4plebs"]``),
    each URL is treated as a web page.  The extractor is called with the page URL
    and its HTML; it must return a list of direct image URLs to download.  Use
    this for datasets where the URL column links to archive/thread pages rather
    than direct image files.

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
            if page_image_extractor is not None:
                future_to_row = {
                    executor.submit(
                        _scrape_one_page,
                        row["url"],
                        page_image_extractor,
                        images_dir,
                        row["metadata"],
                        timeout,
                    ): row
                    for row in rows
                }
            else:
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
                raw = future.result()
                # _download_one → dict; _scrape_one_page → List[dict]
                batch: List[dict] = raw if isinstance(raw, list) else [raw]

                n_ok = sum(1 for r in batch if r["status"] == "downloaded")
                n_sk = sum(1 for r in batch if r["status"] == "skipped")
                n_fail = sum(1 for r in batch if r["status"] == "failed")

                downloaded.extend(r for r in batch if r["status"] == "downloaded")
                skipped.extend(r for r in batch if r["status"] == "skipped")
                failed.extend(r for r in batch if r["status"] == "failed")

                parts = []
                if n_ok:
                    parts.append(f"[green]{n_ok} ok[/]")
                if n_sk:
                    parts.append(f"[yellow]{n_sk} skip[/]")
                if n_fail:
                    parts.append(f"[red]{n_fail} fail[/]")
                progress.update(task, advance=1, status=" ".join(parts))

    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
    }
