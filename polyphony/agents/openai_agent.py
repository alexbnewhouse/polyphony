"""
polyphony.agents.openai_agent
============================
LLM coder backed by any OpenAI-compatible API (OpenAI, Azure OpenAI, vLLM, etc.).

Replicability notes:
- ``temperature`` is passed in every request for consistent outputs.
- ``seed`` is passed when supported by the API, but not all providers honour it.
  OpenAI documents seed-based determinism as "best effort" — responses may still
  vary across API versions.  Always check ``system_fingerprint`` in the response.
- ``model_version`` is recorded from the ``model`` field in the API response,
  which may include a snapshot date (e.g. ``gpt-4o-2024-08-06``).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import openai as _openai
except ImportError:
    _openai = None  # type: ignore[assignment]

from ._image_utils import EXT_TO_MIME, encode_image_base64
from .base import BaseAgent, parse_json


class OpenAIAgent(BaseAgent):
    """
    An LLM coder using an OpenAI-compatible API.

    Parameters
    ----------
    agent_id : int
    project_id : int
    role : str
    model_name : str
        Model identifier, e.g. ``gpt-4o``, ``gpt-4o-mini``.
    temperature : float
        Sampling temperature (recommend 0.0-0.2 for coding consistency).
    seed : int
        Deterministic seed.  Passed to the API but support varies by provider.
    conn : sqlite3.Connection
    api_key : str | None
        Explicit API key.  Falls back to ``POLYPHONY_OPENAI_API_KEY`` then
        ``OPENAI_API_KEY`` environment variables.
    base_url : str | None
        Override the base URL for API-compatible endpoints (e.g. Azure, vLLM).
        Falls back to ``OPENAI_BASE_URL`` environment variable.
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
        base_url: Optional[str] = None,
    ):
        if _openai is None:
            raise ImportError(
                "The 'openai' package is required for OpenAI-compatible models. "
                "Install with: pip install 'polyphony[openai]'"
            )

        resolved_key = (
            api_key
            or os.environ.get("POLYPHONY_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not resolved_key:
            raise ValueError(
                "No OpenAI API key found. Set OPENAI_API_KEY or "
                "POLYPHONY_OPENAI_API_KEY environment variable, or pass api_key."
            )

        resolved_base_url = base_url or os.environ.get("OPENAI_BASE_URL") or None

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

        self._client = _openai.OpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
        )

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        images: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Send a chat completion request to an OpenAI-compatible API.
        Returns (raw_text, parsed_dict).

        Images are sent as base64-encoded data URLs for vision-capable models.
        """
        # Build user message content
        if images:
            content: Any = [{"type": "text", "text": user_prompt}]
            for img_path in images:
                b64 = encode_image_base64(img_path)
                ext = Path(img_path).suffix.lstrip(".").lower()
                mime = EXT_TO_MIME.get(ext, "image/png")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            user_message: Dict[str, Any] = {"role": "user", "content": content}
        else:
            user_message = {"role": "user", "content": user_prompt}

        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    user_message,
                ],
                temperature=self.temperature,
                seed=self.seed,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            raise RuntimeError(
                f"OpenAI API call failed for model '{self.model_name}'. "
                f"Original error: {e}"
            ) from e

        # Record the model version from the response
        if response.model:
            self.model_version = response.model

        raw = response.choices[0].message.content or ""
        parsed = parse_json(raw)
        return raw, parsed

    def is_available(self) -> bool:
        """Return True if the API is reachable with valid credentials."""
        try:
            self._client.models.list()
            return True
        except Exception:
            return False
