"""Tests for polyphony.io.fetchers — CSV image URL downloading."""

from __future__ import annotations

import csv
import http.server
import threading
import urllib.error
import urllib.request
from pathlib import Path

from unittest.mock import patch

import pytest

from polyphony.io.fetchers import (
    _is_safe_host,
    _sanitize_filename,
    SafeRedirectHandler,
    fetch_images_from_csv,
    extract_html_images,
    extract_4plebs_images,
    PAGE_EXTRACTORS,
)
from polyphony.io.importers import sha256_bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_png() -> bytes:
    """Return a valid 1x1 red PNG (67 bytes)."""
    import struct
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw_data = b"\x00\xff\x00\x00"  # filter byte + RGB
    idat = _chunk(b"IDAT", zlib.compress(raw_data))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class _ImageServer:
    """Tiny HTTP server that serves PNGs and HTML pages for testing."""

    def __init__(self, tmp_path: Path):
        self.png_data = _make_minimal_png()

        parent = self  # closure reference

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.endswith(".png"):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.end_headers()
                    self.wfile.write(parent.png_data)
                elif self.path == "/timeout":
                    import time
                    time.sleep(60)
                elif self.path == "/thread/123/":
                    # Simulated thread archive page with two image links
                    port = self.server.server_address[1]
                    html = (
                        "<html><body>"
                        f'<a href="http://127.0.0.1:{port}/a.png">img1</a>'
                        f'<a href="http://127.0.0.1:{port}/b.png">img2</a>'
                        # thumbnail link — should NOT be included by 4plebs extractor
                        f'<img src="http://127.0.0.1:{port}/1234567890123s.png">'
                        # full-size link — should be included by 4plebs extractor
                        # (uses fake 4pcdn hostname, tested separately)
                        "</body></html>"
                    )
                    body = html.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/empty_thread/":
                    body = b"<html><body><p>No images here</p></body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # suppress logs

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def image_server(tmp_path):
    srv = _ImageServer(tmp_path).start()
    # Patch _is_safe_host to allow localhost during tests
    with patch("polyphony.io.fetchers._is_safe_host", return_value=True):
        yield srv
    srv.stop()


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestIsSafeHost:
    def test_blocks_localhost(self):
        assert _is_safe_host("localhost") is False
        assert _is_safe_host("127.0.0.1") is False

    def test_blocks_metadata_endpoint(self):
        assert _is_safe_host("169.254.169.254") is False

    def test_blocks_private_ips(self):
        assert _is_safe_host("10.0.0.1") is False
        assert _is_safe_host("192.168.1.1") is False

    def test_blocks_empty(self):
        assert _is_safe_host("") is False

    def test_blocks_hostname_resolving_to_private_ip(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(None, None, None, None, ("10.0.0.9", 0))],
        )
        assert _is_safe_host("example.internal") is False

    def test_allows_hostname_resolving_to_public_ip(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))],
        )
        assert _is_safe_host("example.com") is True


class TestSanitizeFilename:
    def test_simple_url(self):
        assert _sanitize_filename("https://example.com/photo.png") == "photo.png"

    def test_url_with_query(self):
        assert _sanitize_filename("https://example.com/img.jpg?size=large") == "img.jpg"

    def test_no_filename(self):
        assert _sanitize_filename("https://example.com/") == "image"

    def test_empty_path(self):
        assert _sanitize_filename("https://example.com") == "image"

    def test_filename_is_length_bounded(self):
        long_name = "a" * 500 + ".png"
        sanitized = _sanitize_filename(f"https://example.com/{long_name}")
        assert len(sanitized) <= 240


class TestSha256Bytes:
    def test_deterministic(self):
        data = b"test data"
        assert sha256_bytes(data) == sha256_bytes(data)

    def test_different_data(self):
        assert sha256_bytes(b"a") != sha256_bytes(b"b")


class TestSafeRedirectHandler:
    def test_rejects_non_http_redirect(self):
        handler = SafeRedirectHandler()
        req = urllib.request.Request("https://example.com/start.png")

        with pytest.raises(urllib.error.URLError, match="unsupported scheme"):
            handler.redirect_request(
                req,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="file:///etc/passwd",
            )

    def test_rejects_unsafe_host_redirect(self, monkeypatch):
        monkeypatch.setattr("polyphony.io.fetchers._is_safe_host", lambda hostname: False)
        handler = SafeRedirectHandler()
        req = urllib.request.Request("https://example.com/start.png")

        with pytest.raises(urllib.error.URLError, match="unsafe host"):
            handler.redirect_request(
                req,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://metadata.google.internal/instance",
            )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestFetchImagesFromCsv:
    def test_downloads_images(self, tmp_path, image_server):
        """Fetch images from a CSV and verify they are saved."""
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(
            csv_path,
            [
                {"url": f"{image_server.base_url}/a.png", "label": "first"},
                {"url": f"{image_server.base_url}/b.png", "label": "second"},
            ],
            fieldnames=["url", "label"],
        )

        result = fetch_images_from_csv(csv_path, images_dir, url_column="url")
        # Both download because filenames differ (a.png vs b.png) even though content is same
        assert len(result["downloaded"]) == 2
        assert len(result["failed"]) == 0

        # Verify file exists
        downloaded = result["downloaded"][0]
        assert downloaded["path"].exists()
        assert downloaded["path"].parent == images_dir

    def test_metadata_columns(self, tmp_path, image_server):
        """Selected metadata columns are passed through."""
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(
            csv_path,
            [{"url": f"{image_server.base_url}/a.png", "label": "cat", "source": "web"}],
            fieldnames=["url", "label", "source"],
        )

        result = fetch_images_from_csv(
            csv_path, images_dir, metadata_columns=["label"],
        )
        meta = result["downloaded"][0]["metadata"]
        assert meta["label"] == "cat"
        assert "source" not in meta

    def test_all_metadata_by_default(self, tmp_path, image_server):
        """Without metadata_columns, all non-URL columns are included."""
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(
            csv_path,
            [{"url": f"{image_server.base_url}/a.png", "label": "cat", "source": "web"}],
            fieldnames=["url", "label", "source"],
        )

        result = fetch_images_from_csv(csv_path, images_dir)
        meta = result["downloaded"][0]["metadata"]
        assert meta["label"] == "cat"
        assert meta["source"] == "web"

    def test_missing_url_column_raises(self, tmp_path):
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(csv_path, [{"link": "http://x.com/a.png"}], fieldnames=["link"])

        with pytest.raises(ValueError, match="URL column.*not found"):
            fetch_images_from_csv(csv_path, images_dir, url_column="url")

    def test_bad_scheme_fails(self, tmp_path):
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(csv_path, [{"url": "ftp://example.com/a.png"}], fieldnames=["url"])

        result = fetch_images_from_csv(csv_path, images_dir)
        assert len(result["failed"]) == 1
        assert "Unsupported scheme" in result["failed"][0]["error"]

    def test_404_fails(self, tmp_path, image_server):
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(
            csv_path,
            [{"url": f"{image_server.base_url}/nonexistent.txt"}],
            fieldnames=["url"],
        )

        result = fetch_images_from_csv(csv_path, images_dir)
        assert len(result["failed"]) == 1

    def test_empty_csv(self, tmp_path):
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(csv_path, [], fieldnames=["url"])

        result = fetch_images_from_csv(csv_path, images_dir)
        assert result == {"downloaded": [], "skipped": [], "failed": []}

    def test_deduplication(self, tmp_path, image_server):
        """Same image URL twice produces one download and one skip."""
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        url = f"{image_server.base_url}/same.png"
        _write_csv(csv_path, [{"url": url}, {"url": url}], fieldnames=["url"])

        result = fetch_images_from_csv(csv_path, images_dir, max_concurrent=1)
        total = len(result["downloaded"]) + len(result["skipped"])
        assert total == 2
        assert len(result["downloaded"]) >= 1


# ---------------------------------------------------------------------------
# Page image extractor tests
# ---------------------------------------------------------------------------


class TestExtractHtmlImages:
    def test_finds_href_links(self):
        html = '<a href="/photo.jpg">click</a>'
        urls = extract_html_images("http://example.com/page", html)
        assert urls == ["http://example.com/photo.jpg"]

    def test_finds_img_src(self):
        html = '<img src="https://cdn.example.com/banner.png">'
        urls = extract_html_images("http://example.com/", html)
        assert urls == ["https://cdn.example.com/banner.png"]

    def test_skips_non_image_hrefs(self):
        html = '<a href="/about">About</a><a href="/pic.png">Pic</a>'
        urls = extract_html_images("http://example.com/", html)
        assert len(urls) == 1
        assert urls[0].endswith("pic.png")

    def test_skips_non_http_schemes(self):
        html = '<a href="ftp://files.example.com/img.jpg">ftp</a>'
        urls = extract_html_images("http://example.com/", html)
        assert urls == []

    def test_deduplicates(self):
        html = '<a href="/x.jpg">1</a><a href="/x.jpg">2</a>'
        urls = extract_html_images("http://example.com/", html)
        assert urls == ["http://example.com/x.jpg"]

    def test_resolves_relative_urls(self):
        html = '<a href="../images/photo.png">p</a>'
        urls = extract_html_images("http://example.com/blog/post/", html)
        assert urls == ["http://example.com/blog/images/photo.png"]


class TestExtract4plebsImages:
    _FULL = "https://i.4pcdn.org/pol/1614460319054.jpg"
    _THUMB = "https://i.4pcdn.org/pol/1636483147264s.jpg"

    def test_collects_full_size(self):
        html = f'<a href="{self._FULL}">view</a>'
        urls = extract_4plebs_images("https://archive.4plebs.org/pol/thread/1/", html)
        assert urls == [self._FULL]

    def test_skips_thumbnails(self):
        html = f'<img src="{self._THUMB}"><a href="{self._FULL}">view</a>'
        urls = extract_4plebs_images("https://archive.4plebs.org/pol/thread/1/", html)
        assert self._THUMB not in urls
        assert self._FULL in urls

    def test_skips_other_hosts(self):
        html = '<a href="https://example.com/photo.jpg">ext</a>'
        urls = extract_4plebs_images("https://archive.4plebs.org/pol/thread/1/", html)
        assert urls == []

    def test_deduplicates(self):
        html = f'<a href="{self._FULL}">1</a><a href="{self._FULL}">2</a>'
        urls = extract_4plebs_images("https://archive.4plebs.org/pol/thread/1/", html)
        assert len(urls) == 1

    def test_page_extractors_registry(self):
        assert "4plebs" in PAGE_EXTRACTORS
        assert PAGE_EXTRACTORS["4plebs"] is extract_4plebs_images
        assert "generic" in PAGE_EXTRACTORS
        assert PAGE_EXTRACTORS["generic"] is extract_html_images


class TestFetchImagesWithPageScraper:
    def test_scrapes_images_from_html_page(self, tmp_path, image_server):
        """Scraper follows <a href> links on a page and downloads images."""
        csv_path = tmp_path / "threads.csv"
        images_dir = tmp_path / "images"
        page_url = f"{image_server.base_url}/thread/123/"
        _write_csv(csv_path, [{"url": page_url, "id": "row1"}], fieldnames=["url", "id"])

        result = fetch_images_from_csv(
            csv_path,
            images_dir,
            url_column="url",
            page_image_extractor=extract_html_images,
        )

        assert len(result["failed"]) == 0
        assert len(result["downloaded"]) >= 1
        for entry in result["downloaded"]:
            assert entry["path"].exists()
            assert entry["metadata"].get("page_url") == page_url

    def test_page_with_no_images_fails_gracefully(self, tmp_path, image_server):
        csv_path = tmp_path / "threads.csv"
        images_dir = tmp_path / "images"
        page_url = f"{image_server.base_url}/empty_thread/"
        _write_csv(csv_path, [{"url": page_url}], fieldnames=["url"])

        result = fetch_images_from_csv(
            csv_path, images_dir, page_image_extractor=extract_html_images,
        )

        assert len(result["failed"]) == 1
        assert "No images found" in result["failed"][0]["error"]

    def test_direct_image_url_still_works_with_extractor(self, tmp_path, image_server):
        """If URL resolves to an image (not HTML), extractor still downloads it."""
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(
            csv_path,
            [{"url": f"{image_server.base_url}/direct.png"}],
            fieldnames=["url"],
        )

        result = fetch_images_from_csv(
            csv_path, images_dir, page_image_extractor=extract_html_images,
        )

        assert len(result["downloaded"]) == 1
        assert len(result["failed"]) == 0

    def test_bad_scheme_fails_with_extractor(self, tmp_path):
        csv_path = tmp_path / "urls.csv"
        images_dir = tmp_path / "images"
        _write_csv(csv_path, [{"url": "ftp://example.com/thread/1/"}], fieldnames=["url"])

        result = fetch_images_from_csv(
            csv_path, images_dir, page_image_extractor=extract_html_images,
        )

        assert len(result["failed"]) == 1
        assert "Unsupported scheme" in result["failed"][0]["error"]

    def test_cloudscraper_ssrf_redirect_blocked(self):
        """cloudscraper redirect to a private IP must be blocked by SSRF check."""
        import types
        import cloudscraper as _cs

        # Fake response where history contains a redirect to 10.0.0.1 (private)
        fake_redirect = types.SimpleNamespace(
            headers={"Location": "http://10.0.0.1/secret"},
            url="http://example.com/start",
        )
        fake_final = types.SimpleNamespace(
            history=[fake_redirect],
            headers={"Content-Type": "text/html"},
            status_code=200,
            text="<html></html>",
        )
        fake_final.raise_for_status = lambda: None

        class _FakeSession:
            def get(self, url, timeout=30):
                return fake_final

        from polyphony.io.fetchers import _fetch_page_html
        with patch.object(_cs, "create_scraper", return_value=_FakeSession()):
            with pytest.raises(Exception, match="unsafe host"):
                _fetch_page_html("http://example.com/start", timeout=5)

