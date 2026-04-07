"""
polyphony.io.transcribers
========================
Audio transcription helpers for ingesting interview recordings.

This module is intentionally independent from the coding pipeline:
- It transcribes audio into text.
- It optionally performs speaker diarization.
- It records provenance metadata including audio timestamps.
- The resulting transcript text can then be imported with existing text workflows.
"""

from __future__ import annotations

import os
import re
import shutil
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from .importers import sha256_bytes

try:
    from faster_whisper import WhisperModel as _WhisperModel  # type: ignore[import-not-found]
except ImportError:
    _WhisperModel = None  # type: ignore[assignment]

try:
    import openai as _openai  # type: ignore[import-not-found]
except ImportError:
    _openai = None  # type: ignore[assignment]

AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".wav",
        ".m4a",
        ".mp4",
        ".mpeg",
        ".mpga",
        ".webm",
        ".ogg",
        ".flac",
        ".aac",
    }
)

SUPPORTED_PROVIDERS = frozenset({"local_whisper", "openai"})
_DEFAULT_LOCAL_MODEL = "small"
_DEFAULT_OPENAI_MODEL = "whisper-1"
_MAX_OPENAI_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_AUDIO_BYTES = 500 * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────────────
# Diarization helpers
# ─────────────────────────────────────────────────────────────────────────────


def _run_diarization(
    audio_path: Path,
    *,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Run speaker diarization on an audio file using pyannote.audio.

    Returns a list of dicts with keys: start, end, speaker.
    Requires the pyannote.audio package and a Hugging Face auth token
    (set HF_TOKEN or HUGGING_FACE_HUB_TOKEN).
    """
    try:
        from pyannote.audio import Pipeline as _PyannotePipeline  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError(
            "pyannote.audio is required for speaker diarization. "
            "Install with: pip install 'polyphony[diarize]'"
        )

    import os as _os
    hf_token = (
        _os.environ.get("HF_TOKEN")
        or _os.environ.get("HUGGING_FACE_HUB_TOKEN")
    )
    if not hf_token:
        raise ValueError(
            "Speaker diarization requires a Hugging Face token. "
            "Set HF_TOKEN or HUGGING_FACE_HUB_TOKEN. "
            "You must also accept the pyannote model license at "
            "https://huggingface.co/pyannote/speaker-diarization-3.1"
        )

    pipeline = _PyannotePipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )

    kwargs: Dict[str, Any] = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    diarization = pipeline(str(audio_path), **kwargs)

    result: List[Dict[str, Any]] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        result.append({
            "start": round(float(turn.start), 3),
            "end": round(float(turn.end), 3),
            "speaker": speaker,
        })
    return result


def _assign_speakers_to_segments(
    whisper_segments: List[Dict[str, Any]],
    diarization_turns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Assign speaker labels from diarization to Whisper transcription segments.

    Uses overlap-based matching: each Whisper segment is assigned to the
    diarization speaker with the most temporal overlap.
    """
    for seg in whisper_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        best_speaker = "UNKNOWN"
        best_overlap = 0.0

        for turn in diarization_turns:
            overlap_start = max(seg_start, turn["start"])
            overlap_end = min(seg_end, turn["end"])
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = turn["speaker"]

        seg["speaker"] = best_speaker

    return whisper_segments


def _format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS for display."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _normalize_language(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    value = language.strip()
    if not value:
        return None
    if not re.match(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$", value):
        raise ValueError(
            "Invalid language code. Use ISO-style tags like 'en', 'es', or 'pt-BR'."
        )
    return value.lower()


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    safe = safe.strip("._")
    return safe or "audio"


def _probe_duration_seconds(path: Path) -> Optional[float]:
    # Standard library can probe WAV reliably without external dependencies.
    if path.suffix.lower() not in {".wav", ".wave"}:
        return None
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return round(frames / float(rate), 3)
    except Exception:
        return None


def store_audio_file(
    source_path: Path,
    audio_dir: Path,
    max_bytes: int = _DEFAULT_MAX_AUDIO_BYTES,
) -> Dict[str, Any]:
    """Copy an audio file into the project and return provenance metadata."""
    source_path = Path(source_path)
    audio_dir = Path(audio_dir)

    file_size = source_path.stat().st_size
    if file_size > max_bytes:
        raise ValueError(
            f"Audio file exceeds maximum allowed size ({max_bytes} bytes): {source_path.name}"
        )

    raw = source_path.read_bytes()
    if not raw:
        raise ValueError(f"Audio file is empty: {source_path}")

    audio_hash = sha256_bytes(raw)
    audio_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{audio_hash[:12]}_{_safe_filename(source_path.name)}"
    stored_path = audio_dir / stored_name
    if not stored_path.exists():
        shutil.copy2(source_path, stored_path)

    return {
        "original_filename": source_path.name,
        "stored_audio_path": str(stored_path),
        "audio_sha256": audio_hash,
        "file_size_bytes": len(raw),
        "audio_format": source_path.suffix.lower().lstrip("."),
        "duration_seconds": _probe_duration_seconds(source_path),
    }


SUPPORTED_COMPUTE_TYPES = frozenset({"int8", "float16", "float32", "auto"})


def _select_compute_type(
    compute_type: Optional[str] = None,
    device: str = "auto",
) -> str:
    """Pick an appropriate CTranslate2 compute type for the device.

    - ``"auto"`` (default): ``float16`` when CUDA is available, ``int8`` on CPU.
    - An explicit value (``"int8"``, ``"float16"``, ``"float32"``) is returned as-is.
    """
    if compute_type is not None and compute_type != "auto":
        ct = compute_type.strip().lower()
        if ct not in SUPPORTED_COMPUTE_TYPES:
            raise ValueError(
                f"Unsupported compute_type '{compute_type}'. "
                f"Choose from: {', '.join(sorted(SUPPORTED_COMPUTE_TYPES))}"
            )
        return ct

    # Auto-detect: prefer float16 on CUDA, int8 on CPU
    if device == "cuda":
        return "float16"
    if device == "auto":
        try:
            import ctranslate2  # type: ignore[import-not-found]

            if ctranslate2.get_cuda_device_count() > 0:
                return "float16"
        except Exception:
            pass
    return "int8"


def _transcribe_local_whisper(
    audio_path: Path,
    *,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
    compute_type: Optional[str] = None,
    diarize: bool = False,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> Dict[str, Any]:
    if _WhisperModel is None:
        raise ImportError(
            "faster-whisper is required for local transcription. "
            "Install with: pip install 'polyphony[audio]'"
        )

    resolved_ct = _select_compute_type(compute_type, device="auto")
    whisper = _WhisperModel(model_size_or_path=model, device="auto", compute_type=resolved_ct)
    segments_iter, info = whisper.transcribe(
        str(audio_path),
        language=language,
        initial_prompt=prompt or None,
    )

    pieces: List[str] = []
    segments: List[Dict[str, Any]] = []

    for seg in segments_iter:
        text = (getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        pieces.append(text)
        segments.append(
            {
                "start": round(float(getattr(seg, "start", 0.0)), 3),
                "end": round(float(getattr(seg, "end", 0.0)), 3),
                "text": text,
            }
        )

    transcript = "\n\n".join(pieces).strip()
    detected_language = getattr(info, "language", None)
    duration = getattr(info, "duration", None)

    # Diarization: assign speaker labels to each segment
    diarization_applied = False
    if diarize:
        try:
            diarization_result = _run_diarization(
                audio_path,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            segments = _assign_speakers_to_segments(segments, diarization_result)
            diarization_applied = True
            # Rebuild transcript with speaker labels
            labelled_pieces = []
            for seg in segments:
                speaker = seg.get("speaker", "UNKNOWN")
                labelled_pieces.append(f"[{speaker}]: {seg['text']}")
            transcript = "\n\n".join(labelled_pieces).strip()
        except ImportError as exc:
            import warnings
            warnings.warn(
                f"Diarization skipped (missing dependency): {exc}. "
                "Install with: pip install 'polyphony[diarize]'"
            )
        except Exception as exc:
            import warnings
            warnings.warn(f"Diarization failed, proceeding without speaker labels: {exc}")

    return {
        "text": transcript,
        "model": model,
        "language": detected_language or language,
        "duration_seconds": float(duration) if duration is not None else None,
        "segments": segments,
        "diarization_applied": diarization_applied,
    }


def _transcribe_openai(
    audio_path: Path,
    *,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
) -> Dict[str, Any]:
    if _openai is None:
        raise ImportError(
            "openai package is required for OpenAI transcription. "
            "Install with: pip install 'polyphony[openai]'"
        )

    api_key = os.environ.get("POLYPHONY_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "No OpenAI API key found. Set OPENAI_API_KEY or POLYPHONY_OPENAI_API_KEY."
        )

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    client = _openai.OpenAI(api_key=api_key, base_url=base_url)

    with audio_path.open("rb") as audio_file:
        kwargs: Dict[str, Any] = {
            "model": model,
            "file": audio_file,
        }
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt

        response = client.audio.transcriptions.create(**kwargs)

    text = getattr(response, "text", None)
    if text is None and isinstance(response, dict):
        text = response.get("text")

    transcript = (text or "").strip()
    return {
        "text": transcript,
        "model": model,
        "language": language,
        "duration_seconds": None,
        "segments": [],
    }


def transcribe_audio_file(
    source_path: Path,
    *,
    project_audio_dir: Path,
    provider: str = "local_whisper",
    model: Optional[str] = None,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    compute_type: Optional[str] = None,
    max_audio_bytes: int = _DEFAULT_MAX_AUDIO_BYTES,
    diarize: bool = False,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Transcribe one audio file and return transcript + provenance metadata.

    Returns a dict with:
    - text: transcript string
    - metadata: provenance metadata for document.metadata
    - stored_audio_path: canonical copied audio path
    - segments: timestamped transcription chunks (with optional speaker labels)
    - diarization_applied: whether diarization was successfully applied
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Audio file not found: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio file type '{suffix}'. "
            f"Supported: {', '.join(sorted(AUDIO_EXTENSIONS))}"
        )

    file_size = source_path.stat().st_size
    if file_size <= 0:
        raise ValueError(f"Audio file is empty: {source_path}")

    if file_size > max_audio_bytes:
        raise ValueError(
            f"Audio file exceeds maximum allowed size ({max_audio_bytes} bytes): {source_path.name}"
        )

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported transcription provider '{provider}'. "
            "Use 'local_whisper' or 'openai'."
        )

    language_code = _normalize_language(language)

    if provider == "openai" and file_size > _MAX_OPENAI_BYTES:
        raise ValueError(
            "OpenAI transcription currently requires files <= 25 MB. "
            f"Got {file_size} bytes for {source_path.name}."
        )

    audio_meta = store_audio_file(source_path, project_audio_dir, max_bytes=max_audio_bytes)
    stored_audio = Path(audio_meta["stored_audio_path"])

    chosen_model = model
    if provider == "local_whisper":
        chosen_model = model or _DEFAULT_LOCAL_MODEL
        result = _transcribe_local_whisper(
            stored_audio,
            model=chosen_model,
            language=language_code,
            prompt=prompt,
            compute_type=compute_type,
            diarize=diarize,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
    else:
        chosen_model = model or _DEFAULT_OPENAI_MODEL
        result = _transcribe_openai(
            stored_audio,
            model=chosen_model,
            language=language_code,
            prompt=prompt,
        )

    transcript_text = (result.get("text") or "").strip()
    if not transcript_text:
        raise RuntimeError(
            f"Transcription returned empty text for {source_path.name}."
        )

    metadata = {
        "source_type": "audio_transcription",
        "source_audio_path": audio_meta["stored_audio_path"],
        "source_audio_sha256": audio_meta["audio_sha256"],
        "source_audio_filename": audio_meta["original_filename"],
        "source_audio_size_bytes": audio_meta["file_size_bytes"],
        "source_audio_format": audio_meta["audio_format"],
        "source_audio_duration_seconds": audio_meta.get("duration_seconds"),
        "transcription_provider": provider,
        "transcription_model": result.get("model") or chosen_model,
        "transcription_language": result.get("language") or language_code,
        "transcription_duration_seconds": result.get("duration_seconds"),
        "transcription_segment_count": len(result.get("segments", [])),
        "transcription_prompt_used": bool(prompt),
        "diarization_applied": result.get("diarization_applied", False),
    }

    # Collect unique speakers if diarization was applied
    if result.get("diarization_applied"):
        speakers = sorted({
            seg.get("speaker", "UNKNOWN")
            for seg in result.get("segments", [])
            if seg.get("speaker")
        })
        metadata["speakers"] = speakers
        metadata["speaker_count"] = len(speakers)

    return {
        "text": transcript_text,
        "metadata": metadata,
        "stored_audio_path": audio_meta["stored_audio_path"],
        "segments": result.get("segments", []),
        "diarization_applied": result.get("diarization_applied", False),
    }
