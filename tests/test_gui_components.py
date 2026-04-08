"""Tests for polyphony_gui.components — shared UI helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from polyphony_gui.components import (
    _fmt_seconds,
    color_irr_value,
    format_irr_label,
    render_segment,
    style_irr_cell,
)


# ─────────────────────────────────────────────────────────────────────────────
# IRR label formatting
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected_text",
    [
        (0.95, "Excellent"),
        (0.80, "Excellent"),
        (0.70, "Moderate"),
        (0.50, "Poor"),
        (0.0, "Poor"),
    ],
)
def test_format_irr_label_text(value, expected_text):
    label = format_irr_label(value)
    assert expected_text in label


def test_format_irr_label_includes_emoji():
    """WCAG accessibility: labels should include text, not just color."""
    excellent = format_irr_label(0.95)
    poor = format_irr_label(0.3)
    # Both should have some indicator beyond just a number
    assert len(excellent) > 5
    assert len(poor) > 5
    # They should differ
    assert excellent != poor


# ─────────────────────────────────────────────────────────────────────────────
# IRR color coding
# ─────────────────────────────────────────────────────────────────────────────


def test_color_irr_value_high():
    color = color_irr_value(0.9)
    assert isinstance(color, str)
    assert "#" in color


def test_color_irr_value_low():
    color = color_irr_value(0.3)
    assert isinstance(color, str)
    assert "#" in color


def test_color_irr_values_differ():
    """High and low values should map to different colors."""
    assert color_irr_value(0.95) != color_irr_value(0.3)


# ─────────────────────────────────────────────────────────────────────────────
# IRR cell styling
# ─────────────────────────────────────────────────────────────────────────────


def test_style_irr_cell_numeric():
    result = style_irr_cell(0.85)
    assert "background-color" in result


def test_style_irr_cell_string():
    result = style_irr_cell("0.75")
    assert "background-color" in result


def test_style_irr_cell_non_numeric():
    result = style_irr_cell("—")
    assert result == ""


def test_style_irr_cell_none():
    result = style_irr_cell(None)
    assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# _fmt_seconds helper
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secs, expected",
    [
        (0.0, "0:00"),
        (65.9, "1:05"),
        (3600.0, "60:00"),
        (90.0, "1:30"),
    ],
)
def test_fmt_seconds(secs, expected):
    assert _fmt_seconds(secs) == expected


# ─────────────────────────────────────────────────────────────────────────────
# render_segment — unit tests (Streamlit calls mocked)
# ─────────────────────────────────────────────────────────────────────────────


def _make_st_mock():
    """Return a MagicMock that handles ``with st.container(...)`` context."""
    m = MagicMock()
    m.container.return_value.__enter__ = MagicMock(return_value=m)
    m.container.return_value.__exit__ = MagicMock(return_value=False)
    return m


def test_render_segment_image_found(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header
    seg = {
        "media_type": "image",
        "image_path": str(img),
        "filename": "photo.jpg",
        "segment_index": 0,
    }
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg)
    st_mock.image.assert_called_once_with(str(img))


def test_render_segment_image_missing(tmp_path):
    seg = {
        "media_type": "image",
        "image_path": str(tmp_path / "missing.jpg"),
        "filename": "missing.jpg",
        "segment_index": 0,
    }
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg)
    # Should NOT call st.image; should call st.markdown with file-not-found text
    st_mock.image.assert_not_called()
    markdown_calls = " ".join(str(c) for c in st_mock.markdown.call_args_list)
    assert "not found" in markdown_calls.lower()


def test_render_segment_plain_text():
    seg = {
        "media_type": "text",
        "text": "Hello world",
        "filename": "interview.txt",
        "segment_index": 2,
    }
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg)
    st_mock.image.assert_not_called()
    st_mock.audio.assert_not_called()
    # Text should appear somewhere in markdown calls
    all_md = " ".join(str(c) for c in st_mock.markdown.call_args_list)
    assert "Hello world" in all_md


def test_render_segment_text_truncated():
    long_text = "x" * 600
    seg = {"media_type": "text", "text": long_text, "filename": "doc.txt", "segment_index": 0}
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg, truncate=500)
    all_md = " ".join(str(c) for c in st_mock.markdown.call_args_list)
    assert "…" in all_md
    # Should not include the full 600-char text
    assert "x" * 600 not in all_md


def test_render_segment_audio_transcript_with_speaker_and_timestamp(tmp_path):
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"\x00")
    seg = {
        "media_type": "text",
        "text": "So basically yes.",
        "filename": "episode.mp3",
        "segment_index": 1,
        "speaker": "Alice",
        "audio_start_sec": 75.0,
        "audio_end_sec": 82.0,
        "source_path": str(audio),
    }
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg)
    # Speaker + timestamp badge should appear
    all_md = " ".join(str(c) for c in st_mock.markdown.call_args_list)
    assert "Alice" in all_md
    assert "1:15" in all_md  # 75s formatted
    assert "1:22" in all_md  # 82s formatted
    # Audio player should be invoked at the start time
    st_mock.audio.assert_called_once_with(str(audio), start_time=75)


def test_render_segment_audio_transcript_no_file():
    """No st.audio call when source_path is absent."""
    seg = {
        "media_type": "text",
        "text": "Some transcript.",
        "filename": "interview.mp3",
        "segment_index": 0,
        "speaker": "Bob",
        "audio_start_sec": 10.0,
        "audio_end_sec": 20.0,
        # source_path intentionally missing
    }
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg)
    st_mock.audio.assert_not_called()


def test_render_segment_defaults_to_text_when_media_type_absent():
    seg = {"text": "fallback text", "filename": "x.txt", "segment_index": 0}
    st_mock = _make_st_mock()
    with patch("polyphony_gui.components.st", st_mock):
        render_segment(seg)
    st_mock.image.assert_not_called()
    all_md = " ".join(str(c) for c in st_mock.markdown.call_args_list)
    assert "fallback text" in all_md
