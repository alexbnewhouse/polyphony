"""
polyphony.agents.ollama_agent
========================
LLM coder backed by a local Ollama instance.

Replicability notes:
- `seed` is passed in every request for deterministic outputs.
- `model_version` is fetched from Ollama's manifest digest (a content hash
  of the model weights) so the exact model state is recorded.
- Temperature is fixed at the agent's configured value.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, Optional, Tuple

try:
    import ollama as _ollama
except ImportError:
    _ollama = None  # type: ignore[assignment]

from .base import BaseAgent


def get_model_digest(model_name: str, host: str = "http://localhost:11434") -> str:
    """
    Return the Ollama manifest digest for the given model name.
    Falls back to 'unknown' if Ollama is unreachable or the model is missing.
    """
    if _ollama is None:
        return "unknown"
    try:
        client = _ollama.Client(host=host)
        info = client.show(model_name)
        # Ollama returns model info; the digest is in the modelinfo dict
        digest = getattr(info, "modelinfo", {}).get(
            "general.file_type", None
        ) or getattr(info, "digest", "unknown")
        return str(digest) if digest else "unknown"
    except Exception:
        return "unknown"


class OllamaAgent(BaseAgent):
    """
    An LLM coder using a locally-running Ollama model.

    Parameters
    ----------
    agent_id : int
    project_id : int
    role : str
    model_name : str
        Model name as recognised by Ollama, e.g. 'llama3.1:8b'.
    temperature : float
        Sampling temperature (recommend 0.0–0.2 for coding consistency).
    seed : int
        Deterministic seed for reproducible outputs.
    conn : sqlite3.Connection
    host : str
        Ollama server URL (default: http://localhost:11434).
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
        host: str = "http://localhost:11434",
    ):
        if _ollama is None:
            raise ImportError(
                "The 'ollama' package is required. Install with: pip install ollama"
            )

        model_version = get_model_digest(model_name, host)

        super().__init__(
            agent_id=agent_id,
            project_id=project_id,
            role=role,
            model_name=model_name,
            model_version=model_version,
            temperature=temperature,
            seed=seed,
            conn=conn,
        )
        self.host = host
        self._client = _ollama.Client(host=host)

    def _call_llm(
        self, system_prompt: str, user_prompt: str
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Send a chat completion request to Ollama. Returns (raw_text, parsed_dict).
        The response must be valid JSON; if parsing fails, returns {} for parsed.
        """
        try:
            response = self._client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={
                    "temperature": self.temperature,
                    "seed": self.seed,
                },
                format="json",  # Ask Ollama to enforce JSON output where supported
            )
        except Exception as e:
            raise RuntimeError(
                f"Ollama call failed for model '{self.model_name}'. "
                f"Is Ollama running? Try: ollama serve\n"
                f"Original error: {e}"
            ) from e

        raw = response.message.content or ""
        parsed = self._parse_json(raw)
        return raw, parsed

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """
        Extract and parse the first JSON object or array from the response.
        Returns empty dict on failure.
        """
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

    def is_available(self) -> bool:
        """Return True if the Ollama server is reachable and the model is available."""
        try:
            models = self._client.list()
            names = [m.model for m in models.models]
            # Accept both 'llama3.1' and 'llama3.1:8b' as matching 'llama3.1'
            base = self.model_name.split(":")[0]
            return any(m.startswith(base) for m in names)
        except Exception:
            return False
