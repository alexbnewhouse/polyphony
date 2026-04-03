"""Tests for podcast feed parsing, speaker-turn segmentation, and download safety."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from polyphony.io.rss import parse_feed_xml, _parse_itunes_duration
from polyphony.io.importers import (
    _split_speaker_turns,
    parse_speaker_from_segment,
    _merge_whisper_segments_by_speaker,
)
from polyphony.io.podcast import (
    _format_bytes,
    _format_duration,
    _safe_episode_filename,
    _guess_audio_extension,
    preview_podcast_feed,
)


# ─── iTunes duration parsing ───────────────────────────────────────────


class TestParseItunesDuration:
    def test_hh_mm_ss(self):
        assert _parse_itunes_duration("01:30:00") == 5400.0

    def test_mm_ss(self):
        assert _parse_itunes_duration("45:30") == 2730.0

    def test_raw_seconds(self):
        assert _parse_itunes_duration("3600") == 3600.0

    def test_none_returns_none(self):
        assert _parse_itunes_duration(None) is None

    def test_empty_returns_none(self):
        assert _parse_itunes_duration("") is None

    def test_garbage_returns_none(self):
        assert _parse_itunes_duration("not-a-duration") is None


# ─── iTunes RSS namespace parsing ──────────────────────────────────────


PODCAST_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Podcast Show</title>
    <itunes:author>Jane Researcher</itunes:author>
    <itunes:summary>A show about qualitative research.</itunes:summary>
    <itunes:explicit>no</itunes:explicit>
    <itunes:image href="https://example.com/show.jpg"/>
    <itunes:category text="Science">
      <itunes:category text="Social Sciences"/>
    </itunes:category>
    <item>
      <title>Episode One</title>
      <guid>ep-001</guid>
      <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
      <description>First episode description.</description>
      <enclosure url="https://cdn.example.com/ep001.mp3"
                 type="audio/mpeg"
                 length="52428800"/>
      <itunes:duration>01:05:30</itunes:duration>
      <itunes:episode>1</itunes:episode>
      <itunes:season>2</itunes:season>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:author>Jane Researcher</itunes:author>
      <itunes:keywords>qualitative,coding,research</itunes:keywords>
    </item>
    <item>
      <title>Episode Two</title>
      <guid>ep-002</guid>
      <description>Second episode, no audio.</description>
    </item>
  </channel>
</rss>
"""


def test_parse_podcast_rss_extracts_itunes_metadata():
    parsed = parse_feed_xml(PODCAST_RSS_FEED)
    assert parsed["feed_title"] == "Test Podcast Show"
    entries = parsed["entries"]
    assert len(entries) == 2

    # First episode should have full podcast metadata
    ep1 = entries[0]
    assert "podcast" in ep1
    pod = ep1["podcast"]
    assert pod["enclosure_url"] == "https://cdn.example.com/ep001.mp3"
    assert pod["enclosure_type"] == "audio/mpeg"
    assert pod["enclosure_length_bytes"] == 52428800
    assert pod["duration_seconds"] == pytest.approx(3930.0)
    assert pod["episode_number"] == 1
    assert pod["season_number"] == 2
    assert pod["episode_type"] == "full"
    assert "qualitative" in pod["itunes_keywords"]

    # Feed-level podcast metadata
    assert "feed_podcast" in ep1
    fp = ep1["feed_podcast"]
    assert fp["itunes_author"] == "Jane Researcher"

    # Second episode has no enclosure
    ep2 = entries[1]
    pod2 = ep2.get("podcast", {})
    assert not pod2.get("enclosure_url")


# ─── Speaker-turn segmentation ────────────────────────────────────────


class TestSplitSpeakerTurns:
    def test_splits_on_speaker_labels(self):
        text = (
            "[SPEAKER_0]: Hello, I'm the host.\n"
            "[SPEAKER_0]: Welcome to the show.\n"
            "[SPEAKER_1]: Thanks for having me.\n"
            "[SPEAKER_0]: Let's talk about research."
        )
        turns = _split_speaker_turns(text, min_length=5)
        # Each speaker-label match becomes a segment; consecutive same-speaker
        # lines that fall between two labels are part of the same segment.
        assert len(turns) == 4
        # Returns (text, start, end) tuples
        assert "Hello" in turns[0][0]
        assert "Welcome" in turns[1][0]
        assert "Thanks" in turns[2][0]
        assert "research" in turns[3][0]

    def test_falls_back_to_paragraphs_if_no_labels(self):
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        turns = _split_speaker_turns(text, min_length=5)
        assert len(turns) == 3

    def test_filters_short_segments(self):
        text = "[SPEAKER_0]: Hi\n[SPEAKER_1]: A much longer response that exceeds the minimum."
        turns = _split_speaker_turns(text, min_length=20)
        assert len(turns) == 1
        assert "longer response" in turns[0][0]


class TestParseSpeakerFromSegment:
    def test_extracts_speaker_label(self):
        text = "[SPEAKER_0]: Hello there."
        assert parse_speaker_from_segment(text) == "SPEAKER_0"

    def test_returns_none_for_no_label(self):
        text = "Just regular text."
        assert parse_speaker_from_segment(text) is None


class TestMergeWhisperSegmentsBySpeaker:
    def test_merges_consecutive_same_speaker(self):
        segments = [
            {"text": "Hello ", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_0"},
            {"text": "world ", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_0"},
            {"text": "Thanks ", "start": 2.0, "end": 3.0, "speaker": "SPEAKER_1"},
        ]
        merged = _merge_whisper_segments_by_speaker(segments, min_length=5)
        assert len(merged) == 2
        assert "Hello" in merged[0]["text"]
        assert "world" in merged[0]["text"]
        assert merged[0]["start"] == 0.0
        assert merged[0]["end"] == 2.0
        assert merged[1]["speaker"] == "SPEAKER_1"

    def test_preserves_different_speakers(self):
        segments = [
            {"text": "Speaker one text.", "start": 0.0, "end": 1.0, "speaker": "A"},
            {"text": "Speaker two text.", "start": 1.0, "end": 2.0, "speaker": "B"},
            {"text": "Speaker one again.", "start": 2.0, "end": 3.0, "speaker": "A"},
        ]
        merged = _merge_whisper_segments_by_speaker(segments, min_length=5)
        assert len(merged) == 3


# ─── Podcast utility functions ─────────────────────────────────────────


class TestFormatBytes:
    def test_bytes(self):
        assert _format_bytes(500) == "500 B"

    def test_kilobytes(self):
        assert "KB" in _format_bytes(2048)

    def test_megabytes(self):
        assert "MB" in _format_bytes(5 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in _format_bytes(2 * 1024 * 1024 * 1024)

    def test_none(self):
        assert _format_bytes(None) == "unknown"


class TestFormatDuration:
    def test_seconds_only(self):
        assert _format_duration(45) == "0m 45s"

    def test_minutes(self):
        assert _format_duration(130) == "2m 10s"

    def test_hours(self):
        assert "1h" in _format_duration(3661)

    def test_none(self):
        assert _format_duration(None) == "unknown"


class TestSafeEpisodeFilename:
    def test_basic(self):
        result = _safe_episode_filename("My Great Episode!", 1, ".mp3")
        assert result.startswith("001_")
        assert result.endswith(".mp3")
        assert "!" not in result

    def test_empty_title(self):
        result = _safe_episode_filename("", 5, ".m4a")
        assert "episode_5" in result


class TestGuessAudioExtension:
    def test_mp3_url(self):
        assert _guess_audio_extension("https://example.com/ep.mp3", "") == ".mp3"

    def test_m4a_content_type(self):
        assert _guess_audio_extension("https://example.com/ep", "audio/mp4") == ".m4a"

    def test_default_mp3(self):
        assert _guess_audio_extension("https://example.com/ep", "application/octet-stream") == ".mp3"


# ─── Preview with monkeypatched feed ───────────────────────────────────


def test_preview_podcast_feed_returns_estimates(monkeypatch):
    monkeypatch.setattr(
        "polyphony.io.podcast.fetch_rss_entries",
        lambda *args, **kwargs: {
            "feed_title": "Test Podcast",
            "total_entries": 2,
            "entries": [
                {
                    "index": 1,
                    "title": "Ep 1",
                    "podcast": {
                        "enclosure_url": "https://example.com/ep1.mp3",
                        "enclosure_length_bytes": 50_000_000,
                        "duration_seconds": 3600.0,
                    },
                },
                {
                    "index": 2,
                    "title": "Ep 2",
                    "podcast": {
                        "enclosure_url": "https://example.com/ep2.mp3",
                        "enclosure_length_bytes": None,
                        "duration_seconds": 1800.0,
                    },
                },
            ],
        },
    )

    preview = preview_podcast_feed("https://example.com/feed.xml")
    assert preview["feed_title"] == "Test Podcast"
    est = preview["download_estimate"]
    assert est["episodes_with_audio"] == 2
    assert est["episodes_with_size_info"] == 1
    assert est["known_total_bytes"] == 50_000_000
    # Should estimate missing episode based on average
    assert est["estimated_total_bytes"] > est["known_total_bytes"]
    assert est["total_duration_seconds"] == pytest.approx(5400.0)
