"""Tests for RSS/Atom feed parsing and filtering."""

from __future__ import annotations

from email.message import Message

from polyphony.io.rss import entry_to_import_row, fetch_feed_xml, fetch_rss_entries, parse_feed_xml


def test_parse_rss_prefers_content_encoded_over_description():
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\" xmlns:content=\"http://purl.org/rss/1.0/modules/content/\">
  <channel>
    <title>Housing Feed</title>
    <item>
      <title>Interview 1</title>
      <link>https://example.com/interview-1</link>
      <guid>abc123</guid>
      <pubDate>Tue, 01 Apr 2025 10:00:00 GMT</pubDate>
      <description><![CDATA[<p>Short summary only.</p>]]></description>
      <content:encoded><![CDATA[<p>Full transcript excerpt with <b>details</b>.</p>]]></content:encoded>
      <category>housing</category>
    </item>
  </channel>
</rss>
"""
    parsed = parse_feed_xml(feed_xml)
    assert parsed["feed_title"] == "Housing Feed"
    assert len(parsed["entries"]) == 1

    entry = parsed["entries"][0]
    assert entry["content_source"] == "content"
    assert "Full transcript excerpt" in entry["text"]
    assert "<b>" not in entry["text"]
    assert entry["tags"] == ["housing"]


def test_parse_atom_feed_extracts_entry_content_and_link():
    feed_xml = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\">
  <title>Policy Feed</title>
  <entry>
    <title>Policy Note</title>
    <id>tag:example.com,2026:1</id>
    <updated>2026-04-01T10:00:00Z</updated>
    <link rel=\"alternate\" href=\"https://example.com/policy-note\" />
    <summary type=\"html\">&lt;p&gt;Summary text&lt;/p&gt;</summary>
  </entry>
</feed>
"""
    parsed = parse_feed_xml(feed_xml)
    assert parsed["feed_title"] == "Policy Feed"
    assert len(parsed["entries"]) == 1

    entry = parsed["entries"][0]
    assert entry["title"] == "Policy Note"
    assert entry["link"] == "https://example.com/policy-note"
    assert "Summary text" in entry["text"]


def test_fetch_feed_xml_rejects_non_http_scheme():
    try:
        fetch_feed_xml("file:///tmp/feed.xml")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Only http/https" in str(exc)


def test_parse_feed_xml_rejects_dtd_entity_payload():
    payload = """<?xml version=\"1.0\"?>
  <!DOCTYPE rss [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]>
  <rss version=\"2.0\"><channel><title>X</title></channel></rss>
  """
    try:
        parse_feed_xml(payload)
        assert False, "Expected ValueError"
    except ValueError as exc:
      assert "Could not parse feed XML" in str(exc)


def test_fetch_feed_xml_rejects_non_xml_content_type(monkeypatch):
    class _FakeResponse:
        def __init__(self):
            self.headers = Message()
            self.headers.add_header("Content-Type", "application/octet-stream")

        def read(self, *args, **kwargs):
            return b"<rss></rss>"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeOpener:
        def open(self, req, timeout=20):
            return _FakeResponse()

    monkeypatch.setattr("polyphony.io.rss.is_safe_host", lambda hostname: True)
    monkeypatch.setattr("polyphony.io.rss.urllib.request.build_opener", lambda *args: _FakeOpener())

    try:
        fetch_feed_xml("https://example.com/feed.xml")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Unexpected Content-Type" in str(exc)


def test_fetch_feed_xml_rejects_missing_content_type(monkeypatch):
    """A response with no Content-Type must be rejected, not silently accepted."""
    class _FakeResponse:
        def __init__(self):
            self.headers = Message()  # no Content-Type header set

        def read(self, *args, **kwargs):
            return b"<rss></rss>"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeOpener:
        def open(self, req, timeout=20):
            return _FakeResponse()

    monkeypatch.setattr("polyphony.io.rss.is_safe_host", lambda hostname: True)
    monkeypatch.setattr("polyphony.io.rss.urllib.request.build_opener", lambda *args: _FakeOpener())

    try:
        fetch_feed_xml("https://example.com/feed.xml")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Unexpected Content-Type" in str(exc)


def test_fetch_feed_xml_rejects_text_plain_content_type(monkeypatch):
    """text/plain must not be accepted as a valid feed content-type."""
    class _FakeResponse:
        def __init__(self):
            self.headers = Message()
            self.headers.add_header("Content-Type", "text/plain")

        def read(self, *args, **kwargs):
            return b"<rss></rss>"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeOpener:
        def open(self, req, timeout=20):
            return _FakeResponse()

    monkeypatch.setattr("polyphony.io.rss.is_safe_host", lambda hostname: True)
    monkeypatch.setattr("polyphony.io.rss.urllib.request.build_opener", lambda *args: _FakeOpener())

    try:
        fetch_feed_xml("https://example.com/feed.xml")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Unexpected Content-Type" in str(exc)


def test_fetch_rss_entries_filters_by_keyword_and_recency(monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Mixed Feed</title>
    <item>
      <title>Old Housing Story</title>
      <pubDate>Tue, 01 Apr 2003 10:00:00 GMT</pubDate>
      <description>Housing insecurity in archived interview.</description>
    </item>
    <item>
      <title>Recent Housing Story</title>
      <pubDate>Tue, 01 Apr 2030 10:00:00 GMT</pubDate>
      <description>Housing stress and rent burdens in current interviews.</description>
    </item>
  </channel>
</rss>
"""

    monkeypatch.setattr("polyphony.io.rss.fetch_feed_xml", lambda *args, **kwargs: feed_xml)

    result = fetch_rss_entries(
        "https://example.com/feed.xml",
        keywords=["housing", "rent"],
        since_days=365,
        limit=10,
    )

    assert result["feed_title"] == "Mixed Feed"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["title"] == "Recent Housing Story"


def test_fetch_rss_entries_keyword_uses_word_boundary(monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Keyword Feed</title>
    <item>
      <title>Encoded Transcript</title>
      <description>This text says encoded and decoding only.</description>
    </item>
  </channel>
</rss>
"""
    monkeypatch.setattr("polyphony.io.rss.fetch_feed_xml", lambda *args, **kwargs: feed_xml)

    result = fetch_rss_entries("https://example.com/feed.xml", keywords=["code"])
    assert len(result["entries"]) == 0


def test_fetch_rss_entries_tracks_undated_filtered_count(monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Date Feed</title>
    <item>
      <title>No Date Entry</title>
      <description>Entry without pubDate.</description>
    </item>
  </channel>
</rss>
"""
    monkeypatch.setattr("polyphony.io.rss.fetch_feed_xml", lambda *args, **kwargs: feed_xml)

    result = fetch_rss_entries("https://example.com/feed.xml", since_days=30)
    assert len(result["entries"]) == 0
    assert result["undated_filtered_count"] == 1


def test_entry_to_import_row_contains_rss_metadata():
    entry = {
        "title": "Interview Excerpt",
        "guid": "guid-1",
        "link": "https://example.com/post-1",
        "author": "Research Desk",
        "published_at": "2026-04-01T00:00:00+00:00",
        "published_raw": "Tue, 01 Apr 2026 00:00:00 GMT",
        "tags": ["housing", "policy"],
        "content_source": "summary",
        "text": "Transcript-like feed content.",
    }

    row = entry_to_import_row("https://example.com/feed.xml", entry)
    assert row["content"] == "Transcript-like feed content."
    assert row["source_type"] == "rss_feed"
    assert row["feed_entry_guid"] == "guid-1"
    assert row["feed_content_source"] == "summary"
