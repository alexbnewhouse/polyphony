"""
polyphony.agents.base
================
Abstract base class for all coders (human + LLM).

Every call through a BaseAgent is logged to the llm_call table, ensuring
full audit trail and replicability regardless of agent type.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..db import insert, json_col


def parse_json(text: str) -> Dict[str, Any]:
    """
    Extract and parse the first JSON object or array from LLM response text.

    Tries three strategies in order:
    1. Direct ``json.loads`` on the full text.
    2. Extract content from markdown code fences (```json ... ```).
    3. Find the first ``{ ... }`` block via regex.

    Returns an empty dict on failure so callers always get a dict back.
    """
    import re

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON block from markdown code fences
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the first { ... } block
    match = re.search(r"\{[\s\S]+\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


class BaseAgent(ABC):
    """
    Abstract base for all coding agents.

    Subclasses must implement `_call_llm` which does the actual model
    invocation. The base class wraps it with logging, timing, and error
    handling.
    """

    def __init__(
        self,
        agent_id: int,
        project_id: int,
        role: str,
        model_name: str,
        model_version: str,
        temperature: float,
        seed: int,
        conn: sqlite3.Connection,
    ):
        self.agent_id = agent_id
        self.project_id = project_id
        self.role = role
        self.model_name = model_name
        self.model_version = model_version
        self.temperature = temperature
        self.seed = seed
        self._conn = conn

    # ──────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────

    def call(
        self,
        call_type: str,
        system_prompt: str,
        user_prompt: str,
        images: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any], int]:
        """
        Make a call to this agent and return (raw_response, parsed_output, call_id).
        Logs everything to the llm_call table.

        images: optional list of image file paths for multimodal calls.
        """
        start = time.time()
        error = None
        raw_response = ""
        parsed: Dict[str, Any] = {}
        call_id: Optional[int] = None

        # Include image references in the logged prompt for auditability
        logged_user_prompt = user_prompt
        if images:
            logged_user_prompt += "\n\n[Images: " + ", ".join(images) + "]"

        try:
            raw_response, parsed = self._call_llm(system_prompt, user_prompt, images=images)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = int((time.time() - start) * 1000)
            call_id = self._log_call(
                call_type=call_type,
                system_prompt=system_prompt,
                user_prompt=logged_user_prompt,
                full_response=raw_response,
                parsed_output=parsed if not error else None,
                duration_ms=duration_ms,
                error=error,
            )

        return raw_response, parsed, call_id

    # ──────────────────────────────────────────────────────────────────────
    # To be implemented by subclasses
    # ──────────────────────────────────────────────────────────────────────

    @abstractmethod
    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        images: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Invoke the underlying model. Return (raw_text_response, parsed_dict).
        The parsed_dict should contain whatever structured data was extracted
        from the response (e.g. the JSON assignments block).

        images: optional list of image file paths for multimodal models.
        """
        ...

    @property
    def info(self) -> str:
        return f"{self.role} ({self.model_name})"

    # ──────────────────────────────────────────────────────────────────────
    # Logging
    # ──────────────────────────────────────────────────────────────────────

    def _log_call(
        self,
        call_type: str,
        system_prompt: str,
        user_prompt: str,
        full_response: str,
        parsed_output: Optional[Dict],
        duration_ms: int,
        error: Optional[str],
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> int:
        # Hash system+user prompt for prompt sensitivity tracking
        prompt_hash = hashlib.sha256(
            (system_prompt + "\n---\n" + user_prompt).encode("utf-8")
        ).hexdigest()

        row = {
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "call_type": call_type,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "temperature": self.temperature,
            "seed": self.seed,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "full_response": full_response,
            "parsed_output": json_col(parsed_output) if parsed_output else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": duration_ms,
            "error": error,
            "called_at": datetime.now(timezone.utc).isoformat(),
            "prompt_hash": prompt_hash,
        }
        call_id = insert(self._conn, "llm_call", row)
        self._conn.commit()
        return call_id

    _ALLOWED_LINK_COLUMNS = frozenset({"assignment_id", "segment_id", "code_id"})

    def update_call_link(self, call_id: int, **links) -> None:
        """Link an llm_call row to the downstream record it produced."""
        for k in links:
            if k not in self._ALLOWED_LINK_COLUMNS:
                raise ValueError(f"Invalid link column: '{k}'")
        setters = ", ".join(f"{k} = ?" for k in links)
        self._conn.execute(
            f"UPDATE llm_call SET {setters} WHERE id = ?",
            tuple(links.values()) + (call_id,),
        )
        self._conn.commit()
