"""
polyphony.agents.anthropic_agent
===============================
LLM coder backed by the Anthropic Messages API.

Replicability notes:
- ``temperature`` is passed in every request for consistent outputs.
- ``seed`` is *not* supported by the Anthropic API and is ignored.  It is still
  recorded in the ``llm_call`` table for audit trail consistency.
- ``model_version`` is recorded from the ``model`` field in the API response.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None  # type: ignore[assignment]

from ._image_utils import EXT_TO_MIME, encode_image_base64
from .base import BaseAgent, parse_json


class AnthropicAgent(BaseAgent):
    """
    An LLM coder using the Anthropic Messages API.

    Parameters
    ----------
    agent_id : int
    project_id : int
    role : str
    model_name : str
        Model identifier, e.g. ``claude-sonnet-4-5-20250514``.
    temperature : float
        Sampling temperature (recommend 0.0-0.2 for coding consistency).
    seed : int
        Recorded for audit purposes but not sent to the Anthropic API
        (which does not support deterministic seeds).
    conn : sqlite3.Connection
    api_key : str | None
        Explicit API key.  Falls back to ``ANTHROPIC_API_KEY`` environment
        variable.
    max_tokens : int
        Maximum tokens in the response (default 4096).
    """

    def __init__(
        self,
        agent_id: int,
        project_id: int,
        role: str,
        model_name: str,
        temperature: float,
        seed: int,
        conn: sqlite3.Connection,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
    ):
        if _anthropic is None:
            raise ImportError(
                "The 'anthropic' package is required for Anthropic models. "
                "Install with: pip install 'polyphony[anthropic]'"
            )

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY "
                "environment variable, or pass api_key."
            )

        super().__init__(
            agent_id=agent_id,
            project_id=project_id,
            role=role,
            model_name=model_name,
            model_version="unknown",  # updated after first call
            temperature=temperature,
            seed=seed,
            conn=conn,
        )

        self._client = _anthropic.Anthropic(api_key=resolved_key)
        self._max_tokens = max_tokens

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        images: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Send a message to the Anthropic Messages API.
        Returns (raw_text, parsed_dict).

        Images are sent as base64-encoded media blocks.
        """
        # Build user message content
        if images:
            content: list[Any] = [{"type": "text", "text": user_prompt}]
            for img_path in images:
                b64 = encode_image_base64(img_path)
                ext = Path(img_path).suffix.lstrip(".").lower()
                media_type = EXT_TO_MIME.get(ext, "image/png")
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                })
        else:
            content = [{"type": "text", "text": user_prompt}]

        try:
            response = self._client.messages.create(
                model=self.model_name,
                max_tokens=self._max_tokens,
                temperature=self.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as e:
            raise RuntimeError(
                f"Anthropic API call failed for model '{self.model_name}'. "
                f"Original error: {e}"
            ) from e

        # Record the model version from the response
        if response.model:
            self.model_version = response.model

        # Extract text from response content blocks
        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw += block.text

        parsed = parse_json(raw)
        return raw, parsed

    def is_available(self) -> bool:
        """Return True if the API is reachable with valid credentials."""
        try:
            # Minimal call to verify credentials
            self._client.messages.create(
                model=self.model_name,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
