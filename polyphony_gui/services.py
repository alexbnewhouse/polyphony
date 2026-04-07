"""
polyphony_gui.services
======================
Service layer between Streamlit pages and the polyphony pipeline.

This module wraps pipeline calls with:
- Progress callback hooks for Streamlit progress bars
- Standardised error handling (log internally, return user-friendly messages)
- Timeout / cancellation support patterns
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("polyphony_gui")

# ─── Upload limits ────────────────────────────────────────────────────────────

MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024     # 100 MB per file
MAX_TOTAL_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB total
MAX_CODEBOOK_FILE_BYTES = 10 * 1024 * 1024  # 10 MB codebook

REQUIRED_CODEBOOK_FIELDS = {"name"}
VALID_CODE_LEVELS = {"open", "axial", "selective"}


def validate_upload_sizes(files: list) -> Optional[str]:
    """Validate uploaded file sizes.

    Returns an error message string if validation fails, or None if OK.
    """
    total = sum(f.size for f in files)
    if total > MAX_TOTAL_UPLOAD_BYTES:
        limit_mb = MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)
        return f"Total upload size ({total // (1024*1024)} MB) exceeds the {limit_mb} MB limit."
    for f in files:
        if f.size > MAX_FILE_SIZE_BYTES:
            limit_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
            return f"File '{f.name}' ({f.size // (1024*1024)} MB) exceeds the {limit_mb} MB limit."
    return None


def validate_codebook_rows(rows: list[dict]) -> Optional[str]:
    """Validate that codebook rows have required schema.

    Returns an error message if validation fails, or None if OK.
    """
    if not rows:
        return "No code entries found in the uploaded file."
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return f"Row {i + 1} is not a valid code entry."
        name = row.get("name")
        if not name or not str(name).strip():
            return f"Row {i + 1} is missing a code name."
        level = str(row.get("level", "open")).strip().lower()
        if level and level not in VALID_CODE_LEVELS:
            return (
                f"Row {i + 1} has invalid level '{level}'. "
                f"Must be one of: {', '.join(sorted(VALID_CODE_LEVELS))}"
            )
    return None


# ─── Safe error formatting ───────────────────────────────────────────────────

_USER_FRIENDLY_ERRORS = {
    "ConnectionRefusedError": "Could not connect to the AI model. Is Ollama running?",
    "AuthenticationError": "API key is invalid or expired. Check your environment variables.",
    "RateLimitError": "API rate limit exceeded. Wait a moment and try again.",
    "TimeoutError": "The operation timed out. Try again or use a faster model.",
    "InsufficientQuotaError": "API quota exceeded. Check your billing.",
}


# Patterns that look like API keys / secrets — redact before showing to users.
_SECRET_PATTERNS = re.compile(
    r"sk-[A-Za-z0-9\-]{20,}"           # OpenAI keys
    r"|sk-ant-[A-Za-z0-9\-]{20,}"      # Anthropic keys
    r"|(?:api[_-]?key|token|authorization|secret)"
    r"[\"']?\s*[:=]\s*[\"']?[^\s\"']+",  # Generic key=value patterns
    re.IGNORECASE,
)


def safe_error_message(e: Exception, context: str = "Operation") -> str:
    """Return a user-friendly error message. Log the full traceback internally."""
    logger.error(f"{context} failed: {type(e).__name__}: {e}", exc_info=True)

    error_type = type(e).__name__
    if error_type in _USER_FRIENDLY_ERRORS:
        return _USER_FRIENDLY_ERRORS[error_type]

    msg = str(e)

    # Redact anything that looks like an API key before further inspection
    msg = _SECRET_PATTERNS.sub("[REDACTED]", msg)

    # Strip common internal prefixes
    if "api_key" in msg.lower() or "api key" in msg.lower():
        return "API authentication failed. Check that your API key is set correctly."
    if "connection" in msg.lower() or "refused" in msg.lower():
        return "Could not connect to the AI provider. Check your network and provider settings."

    # For unknown errors, give a generic message
    return f"{context} failed. Check the logs for details."


# ─── Coding session wrapper ──────────────────────────────────────────────────

@dataclass
class CodingProgress:
    """Track progress of a coding session for UI updates."""
    total: int = 0
    completed: int = 0
    current_segment: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def fraction(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def eta_seconds(self) -> Optional[float]:
        if self.completed == 0:
            return None
        elapsed = time.time() - self.started_at
        rate = self.completed / elapsed
        remaining = self.total - self.completed
        return remaining / rate if rate > 0 else None

    @property
    def eta_display(self) -> str:
        eta = self.eta_seconds
        if eta is None:
            return ""
        if eta < 60:
            return f"~{int(eta)}s remaining"
        return f"~{int(eta // 60)}m {int(eta % 60)}s remaining"


def run_coding_with_progress(
    conn,
    project: dict,
    agent: Any,
    codebook_version_id: int,
    run_type: str = "independent",
    resume: bool = False,
    prompt_key: str = "open_coding",
    progress_callback: Optional[Callable[[CodingProgress], None]] = None,
) -> int:
    """Wrap run_coding_session with progress reporting for the GUI.

    Falls back to the standard pipeline call if progress is not needed.
    """
    from polyphony.pipeline.coding import run_coding_session

    # Use standard call — it uses Rich internally for CLI progress
    return run_coding_session(
        conn=conn,
        project=project,
        agent=agent,
        codebook_version_id=codebook_version_id,
        run_type=run_type,
        resume=resume,
        prompt_key=prompt_key,
    )
