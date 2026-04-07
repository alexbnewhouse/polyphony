"""Tests for polyphony_gui.services — validation, error handling, progress tracking."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polyphony_gui.services import (
    MAX_CODEBOOK_FILE_BYTES,
    MAX_FILE_SIZE_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
    CodingProgress,
    safe_error_message,
    validate_codebook_rows,
    validate_upload_sizes,
)


# ─────────────────────────────────────────────────────────────────────────────
# Upload size validation
# ─────────────────────────────────────────────────────────────────────────────


def _fake_file(name: str, size: int) -> SimpleNamespace:
    """Return a minimal object mimicking Streamlit UploadedFile."""
    return SimpleNamespace(name=name, size=size)


def test_validate_upload_sizes_ok():
    files = [_fake_file("a.txt", 1024), _fake_file("b.csv", 2048)]
    assert validate_upload_sizes(files) is None


def test_validate_upload_sizes_single_too_large():
    big = _fake_file("huge.txt", MAX_FILE_SIZE_BYTES + 1)
    result = validate_upload_sizes([big])
    assert result is not None
    assert "exceeds" in result.lower()


def test_validate_upload_sizes_total_too_large():
    # Each file is within the per-file limit but total exceeds aggregate
    chunk = MAX_FILE_SIZE_BYTES - 1
    n_files = (MAX_TOTAL_UPLOAD_BYTES // chunk) + 2
    files = [_fake_file(f"f{i}.txt", chunk) for i in range(n_files)]
    result = validate_upload_sizes(files)
    assert result is not None
    assert "total" in result.lower()


def test_validate_upload_sizes_empty():
    assert validate_upload_sizes([]) is None


def test_validate_upload_sizes_at_exact_limit():
    f = _fake_file("exact.txt", MAX_FILE_SIZE_BYTES)
    assert validate_upload_sizes([f]) is None


# ─────────────────────────────────────────────────────────────────────────────
# Codebook row validation
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_codebook_rows_ok():
    rows = [
        {"name": "CODE_A", "level": "open", "description": "test"},
        {"name": "CODE_B", "level": "axial"},
    ]
    assert validate_codebook_rows(rows) is None


def test_validate_codebook_rows_empty():
    result = validate_codebook_rows([])
    assert result is not None
    assert "no code" in result.lower()


def test_validate_codebook_rows_missing_name():
    rows = [{"description": "test"}]
    result = validate_codebook_rows(rows)
    assert result is not None
    assert "name" in result.lower()


def test_validate_codebook_rows_blank_name():
    rows = [{"name": "  "}]
    result = validate_codebook_rows(rows)
    assert result is not None
    assert "name" in result.lower()


def test_validate_codebook_rows_invalid_level():
    rows = [{"name": "CODE_A", "level": "bogus"}]
    result = validate_codebook_rows(rows)
    assert result is not None
    assert "bogus" in result.lower()


def test_validate_codebook_rows_non_dict():
    rows = [{"name": "OK"}, "not a dict"]
    result = validate_codebook_rows(rows)
    assert result is not None
    assert "not a valid" in result.lower()


def test_validate_codebook_rows_default_level():
    """Level defaults to 'open' when absent — this should pass."""
    rows = [{"name": "CODE_A"}]
    assert validate_codebook_rows(rows) is None


def test_validate_codebook_rows_case_insensitive_level():
    rows = [{"name": "CODE_A", "level": "Axial"}]
    assert validate_codebook_rows(rows) is None


# ─────────────────────────────────────────────────────────────────────────────
# Safe error messages
# ─────────────────────────────────────────────────────────────────────────────


def test_safe_error_connection_refused():
    e = ConnectionRefusedError("connection refused")
    msg = safe_error_message(e, "Test")
    assert "Ollama" in msg or "connect" in msg.lower()
    # Must NOT leak the raw exception message
    assert "connection refused" not in msg


def test_safe_error_timeout():
    e = TimeoutError("timed out")
    msg = safe_error_message(e, "Test")
    assert "timed out" in msg.lower() or "timeout" in msg.lower()


def test_safe_error_generic():
    e = RuntimeError("some internal stack trace detail")
    msg = safe_error_message(e, "Import")
    # Must NOT leak the raw error detail
    assert "stack trace" not in msg
    assert "Import" in msg


def test_safe_error_api_key_leak():
    """Error messages containing 'api_key' should be sanitised."""
    e = ValueError("Invalid api_key: sk-abc123xyz")
    msg = safe_error_message(e, "Auth")
    assert "sk-abc123" not in msg
    assert "api" in msg.lower() or "auth" in msg.lower()


def test_safe_error_redacts_openai_key():
    """OpenAI-style sk- keys are redacted from error messages."""
    e = RuntimeError("Bad request with key sk-proj-abcdefghijklmnopqrstuvwxyz123456")
    msg = safe_error_message(e, "Call")
    assert "sk-proj-" not in msg


def test_safe_error_redacts_anthropic_key():
    """Anthropic-style sk-ant- keys are redacted from error messages."""
    e = RuntimeError("Auth failed: sk-ant-api03-AAAAABBBBBCCCCCDDDDDEEEEE")
    msg = safe_error_message(e, "Call")
    assert "sk-ant-" not in msg


def test_safe_error_redacts_generic_token():
    """Generic token=value patterns are redacted."""
    e = RuntimeError("api_key='super_secret_value_12345'")
    msg = safe_error_message(e, "Call")
    assert "super_secret" not in msg


# ─────────────────────────────────────────────────────────────────────────────
# CodingProgress
# ─────────────────────────────────────────────────────────────────────────────


def test_coding_progress_fraction_zero_total():
    p = CodingProgress(total=0, completed=0)
    assert p.fraction == 0.0


def test_coding_progress_fraction():
    p = CodingProgress(total=100, completed=25)
    assert p.fraction == pytest.approx(0.25)


def test_coding_progress_eta_no_work():
    p = CodingProgress(total=100, completed=0)
    assert p.eta_seconds is None
    assert p.eta_display == ""


def test_coding_progress_eta_display():
    import time

    p = CodingProgress(total=100, completed=50, started_at=time.time() - 50)
    eta = p.eta_seconds
    assert eta is not None
    assert eta > 0
    assert "remaining" in p.eta_display
