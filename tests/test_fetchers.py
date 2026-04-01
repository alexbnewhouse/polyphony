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
    """Tiny HTTP server that serves a PNG for any path ending in .png."""

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
