"""Tests for audio transcription helpers."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from polyphony.io.transcribers import store_audio_file, transcribe_audio_file


def _make_wav(path: Path, seconds: int = 1, sample_rate: int = 8000) -> None:
    frames = b"\x00\x00" * (seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


def test_store_audio_file_copies_with_hash_prefix(tmp_path):
    src = tmp_path / "interview.wav"
    _make_wav(src)

    meta = store_audio_file(src, tmp_path / "audio")

    stored = Path(meta["stored_audio_path"])
    assert stored.exists()
    assert stored.name.startswith(meta["audio_sha256"][:12] + "_")
    assert meta["original_filename"] == "interview.wav"
    assert meta["audio_format"] == "wav"
    assert meta["duration_seconds"] is not None


def test_store_audio_file_rejects_oversized_file(tmp_path):
    src = tmp_path / "big.wav"
    _make_wav(src)

    with pytest.raises(ValueError, match="maximum allowed size"):
        store_audio_file(src, tmp_path / "audio", max_bytes=1)


def test_transcribe_audio_file_rejects_invalid_language(tmp_path, monkeypatch):
    src = tmp_path / "clip.wav"
    _make_wav(src)

    monkeypatch.setattr(
        "polyphony.io.transcribers._transcribe_local_whisper",
        lambda *args, **kwargs: {"text": "hello", "segments": [], "model": "small", "language": "en"},
    )

    with pytest.raises(ValueError, match="Invalid language code"):
        transcribe_audio_file(
            src,
            project_audio_dir=tmp_path / "audio",
            provider="local_whisper",
            language="en$",
        )


def test_transcribe_audio_file_rejects_unsupported_extension(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("not audio", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported audio file type"):
        transcribe_audio_file(
            src,
            project_audio_dir=tmp_path / "audio",
            provider="local_whisper",
        )


def test_transcribe_audio_file_openai_size_limit(tmp_path):
    src = tmp_path / "too_large.mp3"
    src.write_bytes(b"0" * (26 * 1024 * 1024))

    with pytest.raises(ValueError, match="<= 25 MB"):
        transcribe_audio_file(
            src,
            project_audio_dir=tmp_path / "audio",
            provider="openai",
        )


def test_transcribe_audio_file_dispatches_local_provider(tmp_path, monkeypatch):
    src = tmp_path / "focus_group.wav"
    _make_wav(src)

    monkeypatch.setattr(
        "polyphony.io.transcribers._transcribe_local_whisper",
        lambda *args, **kwargs: {
            "text": "First line.\n\nSecond line.",
            "segments": [{"start": 0.0, "end": 1.0, "text": "First line."}],
            "model": "small",
            "language": "en",
            "duration_seconds": 1.0,
        },
    )

    result = transcribe_audio_file(
        src,
        project_audio_dir=tmp_path / "audio",
        provider="local_whisper",
        language="en",
    )

    assert "First line" in result["text"]
    assert result["metadata"]["source_type"] == "audio_transcription"
    assert result["metadata"]["transcription_provider"] == "local_whisper"
    assert result["metadata"]["transcription_model"] == "small"
    assert Path(result["stored_audio_path"]).exists()


def test_transcribe_audio_file_rejects_empty_transcript(tmp_path, monkeypatch):
    src = tmp_path / "empty.wav"
    _make_wav(src)

    monkeypatch.setattr(
        "polyphony.io.transcribers._transcribe_local_whisper",
        lambda *args, **kwargs: {"text": "   ", "segments": [], "model": "small", "language": "en"},
    )

    with pytest.raises(RuntimeError, match="empty text"):
        transcribe_audio_file(
            src,
            project_audio_dir=tmp_path / "audio",
            provider="local_whisper",
        )
