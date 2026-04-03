"""
polyphony.io.podcast
===================
End-to-end podcast ingestion pipeline.

Combines RSS feed parsing (with iTunes namespace support), audio download,
transcription, optional diarization, and import into a single workflow.

Safety features:
- Pre-download size estimation from RSS enclosure metadata
- Disk space checks before downloading
- Configurable per-file and total download limits
- SSRF protections inherited from net_safety module
"""

from __future__ import annotations

import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from .net_safety import SafeRedirectHandler, is_safe_host
from .rss import fetch_rss_entries, _parse_itunes_duration

console = Console()

_DEFAULT_MAX_EPISODE_BYTES = 500 * 1024 * 1024  # 500 MB per episode
_DEFAULT_MAX_TOTAL_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB total
_DEFAULT_DOWNLOAD_TIMEOUT = 300  # 5 minutes per episode


def _format_bytes(n: Optional[int]) -> str:
    """Human-readable file size."""
    if n is None or n < 0:
        return "unknown"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _format_duration(seconds: Optional[float]) -> str:
    """Human-readable duration."""
    if seconds is None:
        return "unknown"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _safe_episode_filename(title: str, index: int, ext: str = ".mp3") -> str:
    """Generate a safe filename for a podcast episode."""
    import re
    safe = re.sub(r"[^A-Za-z0-9._\- ]", "_", title)
    safe = safe.strip("._- ")
    safe = re.sub(r"_+", "_", safe)
    if not safe:
        safe = f"episode_{index}"
    # Truncate to reasonable length
    safe = safe[:80]
    return f"{index:03d}_{safe}{ext}"


def preview_podcast_feed(
    feed_url: str,
    *,
    limit: Optional[int] = 50,
    keywords: Optional[List[str]] = None,
    since_days: Optional[int] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Fetch a podcast RSS feed and return episode metadata with download estimates.

    Returns a dict with:
    - feed_title: str
    - episodes: list of episode dicts with podcast metadata
    - total_episodes: int
    - download_estimate: dict with total_bytes, total_duration, episode_count
    """
    result = fetch_rss_entries(
        feed_url,
        timeout=timeout,
        limit=limit,
        keywords=keywords,
        since_days=since_days,
    )

    episodes = result["entries"]
    total_bytes = 0
    total_duration = 0.0
    episodes_with_audio = 0
    episodes_with_size = 0

    for ep in episodes:
        podcast = ep.get("podcast", {})
        if podcast.get("enclosure_url"):
            episodes_with_audio += 1
        enc_bytes = podcast.get("enclosure_length_bytes")
        if enc_bytes:
            total_bytes += enc_bytes
            episodes_with_size += 1
        dur = podcast.get("duration_seconds")
        if dur:
            total_duration += dur

    # Estimate for episodes without size info
    if episodes_with_size > 0 and episodes_with_audio > episodes_with_size:
        avg_bytes = total_bytes / episodes_with_size
        estimated_missing = int(avg_bytes * (episodes_with_audio - episodes_with_size))
        estimated_total = total_bytes + estimated_missing
    else:
        estimated_total = total_bytes
        estimated_missing = 0

    return {
        "feed_url": feed_url,
        "feed_title": result["feed_title"],
        "episodes": episodes,
        "total_episodes": result["total_entries"],
        "shown_episodes": len(episodes),
        "download_estimate": {
            "episodes_with_audio": episodes_with_audio,
            "episodes_with_size_info": episodes_with_size,
            "known_total_bytes": total_bytes,
            "estimated_total_bytes": estimated_total,
            "estimated_missing_bytes": estimated_missing,
            "total_duration_seconds": total_duration,
        },
    }


def print_podcast_preview(preview: Dict[str, Any]) -> None:
    """Print a Rich table of podcast episodes with download estimates."""
    episodes = preview["episodes"]
    estimate = preview["download_estimate"]

    console.print(f"\n[bold]Podcast:[/] {preview['feed_title']}")
    console.print(
        f"[dim]{preview['shown_episodes']} shown "
        f"({preview['total_episodes']} total in feed)[/]"
    )

    # Show download estimate summary
    console.print(f"\n[bold]Download Estimate:[/]")
    console.print(f"  Episodes with audio: {estimate['episodes_with_audio']}")
    if estimate["episodes_with_size_info"] > 0:
        console.print(
            f"  Known total size: {_format_bytes(estimate['known_total_bytes'])} "
            f"({estimate['episodes_with_size_info']} episodes with size info)"
        )
    if estimate["estimated_missing_bytes"] > 0:
        console.print(
            f"  Estimated total (incl. missing): ~{_format_bytes(estimate['estimated_total_bytes'])}"
        )
    if estimate["total_duration_seconds"] > 0:
        console.print(
            f"  Total duration: {_format_duration(estimate['total_duration_seconds'])}"
        )

    # Disk space check
    try:
        disk_usage = shutil.disk_usage(os.path.expanduser("~"))
        free_bytes = disk_usage.free
        console.print(f"  Available disk space: {_format_bytes(free_bytes)}")
        needed = estimate["estimated_total_bytes"] or estimate["known_total_bytes"]
        if needed and needed > free_bytes * 0.8:
            console.print(
                "[red bold]  WARNING: Estimated download may exceed 80% of available disk space![/]"
            )
        elif needed and needed > free_bytes * 0.5:
            console.print(
                "[yellow]  Note: Estimated download will use >50% of available disk space.[/]"
            )
    except OSError:
        pass

    # Episode table
    table = Table(title="Podcast Episodes", show_header=True)
    table.add_column("#", width=4, justify="right")
    table.add_column("Published", width=12)
    table.add_column("S/E", width=6)
    table.add_column("Title", max_width=50)
    table.add_column("Duration", width=10, justify="right")
    table.add_column("Size", width=10, justify="right")
    table.add_column("Audio", width=5, justify="center")

    for ep in episodes:
        podcast = ep.get("podcast", {})
        published = (ep.get("published_at") or ep.get("published_raw") or "")[:10]

        se = ""
        s = podcast.get("season_number")
        e = podcast.get("episode_number")
        if s and e:
            se = f"S{s}E{e}"
        elif e:
            se = f"E{e}"

        duration = _format_duration(podcast.get("duration_seconds"))
        size = _format_bytes(podcast.get("enclosure_length_bytes"))
        has_audio = "✓" if podcast.get("enclosure_url") else "-"

        table.add_row(
            str(ep["index"]),
            published,
            se,
            (ep.get("title", "Untitled"))[:50],
            duration if duration != "unknown" else "-",
            size if size != "unknown" else "-",
            has_audio,
        )

    console.print(table)


def download_episode_audio(
    enclosure_url: str,
    output_dir: Path,
    filename: str,
    *,
    max_bytes: int = _DEFAULT_MAX_EPISODE_BYTES,
    timeout: int = _DEFAULT_DOWNLOAD_TIMEOUT,
) -> Path:
    """
    Download a single podcast episode audio file.

    Returns the path to the downloaded file.
    Raises ValueError for unsafe URLs or oversized files.
    """
    parsed = urlparse(enclosure_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    if not is_safe_host(parsed.hostname or ""):
        raise ValueError("Episode URL host is not allowed for security reasons.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    if output_path.exists():
        return output_path  # Already downloaded (idempotent)

    opener = urllib.request.build_opener(SafeRedirectHandler())
    req = urllib.request.Request(
        enclosure_url,
        headers={"User-Agent": "polyphony-podcast/1.0"},
    )

    try:
        with opener.open(req, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    expected = int(content_length)
                    if expected > max_bytes:
                        raise ValueError(
                            f"Episode file exceeds maximum size "
                            f"({_format_bytes(expected)} > {_format_bytes(max_bytes)})"
                        )
                except ValueError:
                    pass

            # Download to temp file first, then rename (atomic-ish)
            temp_fd, temp_path = tempfile.mkstemp(
                dir=str(output_dir), suffix=".download"
            )
            try:
                total_read = 0
                with os.fdopen(temp_fd, "wb") as temp_file:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total_read += len(chunk)
                        if total_read > max_bytes:
                            raise ValueError(
                                f"Episode download exceeded maximum size "
                                f"({_format_bytes(max_bytes)})"
                            )
                        temp_file.write(chunk)

                os.rename(temp_path, str(output_path))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise

    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download episode: {exc}") from exc

    return output_path


def download_podcast_episodes(
    episodes: List[Dict[str, Any]],
    output_dir: Path,
    *,
    max_per_episode_bytes: int = _DEFAULT_MAX_EPISODE_BYTES,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    timeout: int = _DEFAULT_DOWNLOAD_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    Download audio files for a list of podcast episodes.

    Returns list of dicts with: episode index, title, audio_path, error (if any).
    Includes safety checks for total download size and disk space.
    """
    # Pre-flight: estimate total download size
    total_estimated = sum(
        ep.get("podcast", {}).get("enclosure_length_bytes", 0)
        for ep in episodes
        if ep.get("podcast", {}).get("enclosure_url")
    )

    if total_estimated > max_total_bytes:
        raise ValueError(
            f"Estimated total download ({_format_bytes(total_estimated)}) exceeds "
            f"maximum allowed ({_format_bytes(max_total_bytes)}). "
            "Use --select to choose fewer episodes or increase --max-total-gb."
        )

    # Check disk space
    try:
        disk_usage = shutil.disk_usage(str(output_dir.parent))
        free_bytes = disk_usage.free
        needed = total_estimated or (len(episodes) * 50 * 1024 * 1024)  # assume 50MB avg if unknown
        if needed > free_bytes * 0.9:
            raise ValueError(
                f"Insufficient disk space. Need ~{_format_bytes(needed)}, "
                f"have {_format_bytes(free_bytes)} free."
            )
    except OSError:
        pass

    results: List[Dict[str, Any]] = []
    total_downloaded = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"Downloading {len(episodes)} episode(s)...",
            total=len(episodes),
        )

        for ep in episodes:
            podcast = ep.get("podcast", {})
            enc_url = podcast.get("enclosure_url")

            if not enc_url:
                results.append({
                    "index": ep.get("index"),
                    "title": ep.get("title"),
                    "audio_path": None,
                    "error": "No audio enclosure URL",
                })
                progress.advance(task)
                continue

            # Determine file extension from enclosure URL or type
            ext = _guess_audio_extension(enc_url, podcast.get("enclosure_type", ""))

            filename = _safe_episode_filename(
                ep.get("title", "episode"), ep.get("index", 0), ext
            )

            try:
                # Check cumulative size limit
                enc_size = podcast.get("enclosure_length_bytes", 0)
                if enc_size and (total_downloaded + enc_size) > max_total_bytes:
                    raise ValueError(
                        f"Cumulative download would exceed {_format_bytes(max_total_bytes)} limit"
                    )

                audio_path = download_episode_audio(
                    enc_url,
                    output_dir,
                    filename,
                    max_bytes=max_per_episode_bytes,
                    timeout=timeout,
                )
                actual_size = audio_path.stat().st_size
                total_downloaded += actual_size

                results.append({
                    "index": ep.get("index"),
                    "title": ep.get("title"),
                    "audio_path": str(audio_path),
                    "file_size": actual_size,
                    "error": None,
                })
            except Exception as exc:
                results.append({
                    "index": ep.get("index"),
                    "title": ep.get("title"),
                    "audio_path": None,
                    "error": str(exc),
                })

            progress.advance(task)

    return results


def _guess_audio_extension(url: str, content_type: str) -> str:
    """Guess audio file extension from URL or content type."""
    # Try URL path first
    parsed = urlparse(url)
    path_ext = Path(parsed.path).suffix.lower()
    if path_ext in {".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".mp4", ".webm"}:
        return path_ext

    # Fall back to content type
    type_map = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/flac": ".flac",
        "audio/aac": ".aac",
        "audio/webm": ".webm",
        "video/mp4": ".mp4",
    }
    ct = content_type.lower().split(";")[0].strip()
    return type_map.get(ct, ".mp3")
