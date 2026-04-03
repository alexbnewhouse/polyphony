"""RSS/Atom feed fetching and parsing for data ingestion."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as StdET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from defusedxml import ElementTree as SafeET  # type: ignore[import-not-found]
from defusedxml.common import DefusedXmlException  # type: ignore[import-not-found]

from polyphony.io.net_safety import SafeRedirectHandler, is_safe_host

_MAX_FEED_BYTES = 10 * 1024 * 1024

_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_XML_ALLOWED_MIME_HINTS = ("xml", "rss", "atom")


def _normalize_space(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_itunes_duration(value: Optional[str]) -> Optional[float]:
    """Parse iTunes duration string into seconds.

    Handles formats like:
        "3600"      -> 3600.0  (seconds only)
        "01:30:00"  -> 5400.0  (HH:MM:SS)
        "45:30"     -> 2730.0  (MM:SS)
    """
    if not value:
        return None
    parts = value.strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    return None


class _HTMLTextExtractor(HTMLParser):
    """Convert HTML-ish snippets to readable plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        lowered = tag.lower()
        if lowered in {"script", "style"}:
            self._skip_depth += 1
            return
        if lowered in {"br", "p", "div", "li"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        lowered = tag.lower()
        if lowered in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if lowered in {"p", "div", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_depth > 0:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _html_to_text(value: Optional[str]) -> str:
    text = value or ""
    if not text:
        return ""

    parser = _HTMLTextExtractor()
    parser.feed(text)
    parser.close()
    text = html.unescape(parser.get_text())
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_timestamp(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    # RSS-style dates
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass

    # ISO-style dates (Atom etc.)
    candidate = raw
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt2 = datetime.fromisoformat(candidate)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return dt2.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _find_text(node: StdET.Element, tag: str) -> str:
    elem = node.find(tag)
    if elem is None:
        return ""
    return (elem.text or "").strip()


def _atom_link(entry: StdET.Element) -> str:
    links = entry.findall(f"{{{_ATOM_NS}}}link")
    if not links:
        return ""

    for link in links:
        rel = (link.get("rel") or "alternate").strip().lower()
        href = (link.get("href") or "").strip()
        if rel == "alternate" and href:
            return href

    href = (links[0].get("href") or "").strip()
    return href


def _parse_rss_items(root: StdET.Element) -> tuple[str, List[Dict[str, Any]]]:
    channel = root.find("channel")
    if channel is None:
        return "", []

    feed_title = _normalize_space(_html_to_text(_find_text(channel, "title")))

    # Podcast feed-level metadata (iTunes namespace)
    feed_podcast_meta: Dict[str, Any] = {}
    itunes_author = _find_text(channel, f"{{{_ITUNES_NS}}}author")
    if itunes_author:
        feed_podcast_meta["itunes_author"] = _normalize_space(itunes_author)
    itunes_summary = _find_text(channel, f"{{{_ITUNES_NS}}}summary")
    if itunes_summary:
        feed_podcast_meta["itunes_summary"] = _normalize_space(_html_to_text(itunes_summary))
    itunes_image = channel.find(f"{{{_ITUNES_NS}}}image")
    if itunes_image is not None:
        img_href = (itunes_image.get("href") or "").strip()
        if img_href:
            feed_podcast_meta["itunes_image_url"] = img_href
    itunes_explicit = _find_text(channel, f"{{{_ITUNES_NS}}}explicit")
    if itunes_explicit:
        feed_podcast_meta["itunes_explicit"] = itunes_explicit.strip().lower()
    for cat_elem in channel.findall(f"{{{_ITUNES_NS}}}category"):
        cat_text = (cat_elem.get("text") or "").strip()
        if cat_text:
            feed_podcast_meta.setdefault("itunes_categories", []).append(cat_text)

    items = channel.findall("item")
    entries: List[Dict[str, Any]] = []

    for item in items:
        title = _normalize_space(_html_to_text(_find_text(item, "title")))
        link = _normalize_space(_find_text(item, "link"))
        guid = _normalize_space(_find_text(item, "guid"))
        content_encoded = _html_to_text(_find_text(item, f"{{{_CONTENT_NS}}}encoded"))
        description = _html_to_text(_find_text(item, "description"))
        author = _normalize_space(_find_text(item, f"{{{_DC_NS}}}creator") or _find_text(item, "author"))

        published_raw = _find_text(item, "pubDate") or _find_text(item, f"{{{_DC_NS}}}date")
        published_at = _parse_timestamp(published_raw)

        tags = []
        for cat in item.findall("category"):
            value = _normalize_space(cat.text or "")
            if value:
                tags.append(value)

        text = content_encoded or description or title
        source = "content" if content_encoded else ("summary" if description else "title")

        if not text:
            continue

        # Podcast episode-level metadata (iTunes namespace)
        podcast_meta: Dict[str, Any] = {}

        # Audio enclosure (the actual podcast MP3/M4A file)
        enclosure = item.find("enclosure")
        if enclosure is not None:
            enc_url = (enclosure.get("url") or "").strip()
            enc_type = (enclosure.get("type") or "").strip()
            enc_length = (enclosure.get("length") or "").strip()
            if enc_url:
                podcast_meta["enclosure_url"] = enc_url
                podcast_meta["enclosure_type"] = enc_type
                if enc_length:
                    try:
                        podcast_meta["enclosure_length_bytes"] = int(enc_length)
                    except ValueError:
                        pass

        # iTunes episode metadata
        itunes_ep = _find_text(item, f"{{{_ITUNES_NS}}}episode")
        if itunes_ep:
            try:
                podcast_meta["episode_number"] = int(itunes_ep.strip())
            except ValueError:
                podcast_meta["episode_number_raw"] = itunes_ep.strip()
        itunes_season = _find_text(item, f"{{{_ITUNES_NS}}}season")
        if itunes_season:
            try:
                podcast_meta["season_number"] = int(itunes_season.strip())
            except ValueError:
                podcast_meta["season_number_raw"] = itunes_season.strip()
        itunes_duration = _find_text(item, f"{{{_ITUNES_NS}}}duration")
        if itunes_duration:
            podcast_meta["itunes_duration"] = itunes_duration.strip()
            podcast_meta["duration_seconds"] = _parse_itunes_duration(itunes_duration.strip())
        itunes_ep_type = _find_text(item, f"{{{_ITUNES_NS}}}episodeType")
        if itunes_ep_type:
            podcast_meta["episode_type"] = itunes_ep_type.strip().lower()
        itunes_ep_summary = _find_text(item, f"{{{_ITUNES_NS}}}summary")
        if itunes_ep_summary:
            podcast_meta["itunes_summary"] = _normalize_space(_html_to_text(itunes_ep_summary))
        itunes_ep_author = _find_text(item, f"{{{_ITUNES_NS}}}author")
        if itunes_ep_author:
            podcast_meta["itunes_author"] = _normalize_space(itunes_ep_author)
        itunes_keywords = _find_text(item, f"{{{_ITUNES_NS}}}keywords")
        if itunes_keywords:
            podcast_meta["itunes_keywords"] = [
                kw.strip() for kw in itunes_keywords.split(",") if kw.strip()
            ]
        itunes_ep_explicit = _find_text(item, f"{{{_ITUNES_NS}}}explicit")
        if itunes_ep_explicit:
            podcast_meta["itunes_explicit"] = itunes_ep_explicit.strip().lower()
        itunes_ep_image = item.find(f"{{{_ITUNES_NS}}}image")
        if itunes_ep_image is not None:
            ep_img_href = (itunes_ep_image.get("href") or "").strip()
            if ep_img_href:
                podcast_meta["itunes_image_url"] = ep_img_href

        if not author and podcast_meta.get("itunes_author"):
            author = podcast_meta["itunes_author"]
        if not author and feed_podcast_meta.get("itunes_author"):
            author = feed_podcast_meta["itunes_author"]

        entry: Dict[str, Any] = {
            "title": title or "Untitled",
            "link": link,
            "guid": guid or link,
            "author": author,
            "published_at": published_at,
            "published_raw": published_raw,
            "summary": description,
            "content": content_encoded,
            "text": text,
            "content_source": source,
            "tags": tags,
        }
        if podcast_meta:
            entry["podcast"] = podcast_meta
        if feed_podcast_meta:
            entry["feed_podcast"] = feed_podcast_meta

        entries.append(entry)

    return feed_title, entries


def _parse_atom_items(root: StdET.Element) -> tuple[str, List[Dict[str, Any]]]:
    feed_title = _normalize_space(_html_to_text(_find_text(root, f"{{{_ATOM_NS}}}title")))
    entries = []

    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        title = _normalize_space(_html_to_text(_find_text(entry, f"{{{_ATOM_NS}}}title")))
        link = _atom_link(entry)
        guid = _normalize_space(_find_text(entry, f"{{{_ATOM_NS}}}id"))

        content_elem = entry.find(f"{{{_ATOM_NS}}}content")
        summary_elem = entry.find(f"{{{_ATOM_NS}}}summary")

        content_raw = ""
        summary_raw = ""

        if content_elem is not None:
            content_raw = StdET.tostring(content_elem, encoding="unicode", method="xml")
        if summary_elem is not None:
            summary_raw = StdET.tostring(summary_elem, encoding="unicode", method="xml")

        content = _html_to_text(content_raw)
        summary = _html_to_text(summary_raw)

        author = ""
        author_elem = entry.find(f"{{{_ATOM_NS}}}author")
        if author_elem is not None:
            author = _normalize_space(_find_text(author_elem, f"{{{_ATOM_NS}}}name"))

        published_raw = (
            _find_text(entry, f"{{{_ATOM_NS}}}published")
            or _find_text(entry, f"{{{_ATOM_NS}}}updated")
        )
        published_at = _parse_timestamp(published_raw)

        tags = []
        for cat in entry.findall(f"{{{_ATOM_NS}}}category"):
            term = _normalize_space(cat.get("term") or "")
            if term:
                tags.append(term)

        text = content or summary or title
        source = "content" if content else ("summary" if summary else "title")

        if not text:
            continue

        entries.append(
            {
                "title": title or "Untitled",
                "link": link,
                "guid": guid or link,
                "author": author,
                "published_at": published_at,
                "published_raw": published_raw,
                "summary": summary,
                "content": content,
                "text": text,
                "content_source": source,
                "tags": tags,
            }
        )

    return feed_title, entries


def _is_recent_enough(entry_iso_time: Optional[str], since_days: Optional[int]) -> bool:
    if since_days is None:
        return True
    if since_days <= 0:
        return True
    if not entry_iso_time:
        return False

    try:
        dt = datetime.fromisoformat(entry_iso_time.replace("Z", "+00:00"))
    except ValueError:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    return dt >= cutoff


def _matches_keywords(entry: Dict[str, Any], keywords: List[str]) -> bool:
    if not keywords:
        return True

    haystack = " ".join(
        [
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("content", ""),
            entry.get("text", ""),
            " ".join(entry.get("tags", [])),
        ]
    ).lower()

    for keyword in keywords:
        normalized = keyword.strip().lower()
        if not normalized:
            continue
        if " " in normalized:
            if normalized not in haystack:
                return False
        else:
            if not re.search(rf"\b{re.escape(normalized)}\b", haystack):
                return False
    return True


def fetch_feed_xml(feed_url: str, timeout: int = 20, max_feed_bytes: int = _MAX_FEED_BYTES) -> str:
    """Fetch RSS/Atom XML content from a remote feed URL with SSRF protections."""
    parsed = urlparse(feed_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Unsupported feed URL scheme {parsed.scheme!r}. Only http/https are allowed."
        )

    if not is_safe_host(parsed.hostname or ""):
        raise ValueError("Feed URL host is not allowed for security reasons.")

    opener = urllib.request.build_opener(SafeRedirectHandler())
    req = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": "polyphony-rss/1.0",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )

    try:
        with opener.open(req, timeout=timeout) as response:
            content_type = (response.headers.get("Content-Type", "") or "").lower()
            if not any(hint in content_type for hint in _XML_ALLOWED_MIME_HINTS):
                raise ValueError(
                    f"Unexpected Content-Type for feed URL: {content_type!r}. "
                    "Expected an XML/RSS/Atom content type."
                )

            data = response.read(max_feed_bytes + 1)
            if len(data) > max_feed_bytes:
                raise ValueError(
                    f"Feed exceeds maximum size ({max_feed_bytes // (1024 * 1024)} MB)."
                )

            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to fetch feed URL: {exc}") from exc

    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def parse_feed_xml(feed_xml: str) -> Dict[str, Any]:
    """Parse RSS/Atom XML into normalized entry dictionaries."""
    try:
        root = SafeET.fromstring(feed_xml)
    except (StdET.ParseError, DefusedXmlException) as exc:
        raise ValueError(f"Could not parse feed XML: {exc}") from exc

    tag = root.tag.lower()
    if tag.endswith("rss") or tag == "rss":
        feed_title, entries = _parse_rss_items(root)
    elif tag.endswith("feed"):
        feed_title, entries = _parse_atom_items(root)
    else:
        raise ValueError("Unsupported feed format. Expected RSS or Atom XML.")

    for idx, entry in enumerate(entries, start=1):
        entry["index"] = idx

    return {"feed_title": feed_title or "Untitled Feed", "entries": entries}


def fetch_rss_entries(
    feed_url: str,
    *,
    timeout: int = 20,
    max_feed_bytes: int = _MAX_FEED_BYTES,
    limit: Optional[int] = None,
    keywords: Optional[List[str]] = None,
    since_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Fetch and filter RSS/Atom entries for preview/import workflows."""
    feed_xml = fetch_feed_xml(feed_url, timeout=timeout, max_feed_bytes=max_feed_bytes)
    parsed = parse_feed_xml(feed_xml)

    entries = parsed["entries"]
    filtered = []
    undated_filtered_count = 0

    for entry in entries:
        if not _matches_keywords(entry, keywords or []):
            continue

        if since_days is not None and not entry.get("published_at"):
            undated_filtered_count += 1
            continue

        if not _is_recent_enough(entry.get("published_at"), since_days):
            continue

        filtered.append(entry)

    if limit is not None and limit > 0:
        filtered = filtered[:limit]

    for idx, entry in enumerate(filtered, start=1):
        entry["index"] = idx

    return {
        "feed_url": feed_url,
        "feed_title": parsed["feed_title"],
        "entries": filtered,
        "total_entries": len(entries),
        "undated_filtered_count": undated_filtered_count,
    }


def entry_to_import_row(feed_url: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a normalized feed entry into an import-ready JSON row."""
    metadata = {
        "source_type": "rss_feed",
        "feed_url": feed_url,
        "feed_entry_guid": entry.get("guid"),
        "feed_entry_link": entry.get("link"),
        "feed_entry_title": entry.get("title"),
        "feed_entry_author": entry.get("author"),
        "feed_entry_published_at": entry.get("published_at"),
        "feed_entry_published_raw": entry.get("published_raw"),
        "feed_entry_tags": entry.get("tags", []),
        "feed_content_source": entry.get("content_source"),
    }

    # Include podcast-specific metadata if present
    podcast = entry.get("podcast")
    if podcast:
        metadata["podcast_enclosure_url"] = podcast.get("enclosure_url")
        metadata["podcast_enclosure_type"] = podcast.get("enclosure_type")
        metadata["podcast_enclosure_length_bytes"] = podcast.get("enclosure_length_bytes")
        metadata["podcast_episode_number"] = podcast.get("episode_number")
        metadata["podcast_season_number"] = podcast.get("season_number")
        metadata["podcast_duration"] = podcast.get("itunes_duration")
        metadata["podcast_duration_seconds"] = podcast.get("duration_seconds")
        metadata["podcast_episode_type"] = podcast.get("episode_type")
        metadata["podcast_itunes_summary"] = podcast.get("itunes_summary")
        metadata["podcast_itunes_keywords"] = podcast.get("itunes_keywords")
        metadata["podcast_itunes_explicit"] = podcast.get("itunes_explicit")

    feed_podcast = entry.get("feed_podcast")
    if feed_podcast:
        metadata["podcast_show_author"] = feed_podcast.get("itunes_author")
        metadata["podcast_show_categories"] = feed_podcast.get("itunes_categories")

    row: Dict[str, Any] = {"content": entry.get("text", "")}
    row.update(metadata)
    return row


def write_entries_json(rows: List[Dict[str, Any]], output_path: Path) -> None:
    """Write import rows to a JSON file understood by import_documents()."""
    Path(output_path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
