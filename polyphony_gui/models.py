"""
polyphony_gui.models
=====================
Model discovery utilities for all supported providers.

- Ollama: queries the local REST API to list installed models in real time.
- OpenAI / Anthropic: returns a curated list of recommended models (API does
  not expose a simple public catalog endpoint; the user can always type a
  custom model name).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional


# ─── Known cloud models ───────────────────────────────────────────────────────

# Keep these sorted newest-first so the default selection is the best choice.
OPENAI_MODELS: list[dict] = [
    {"id": "gpt-4o",              "label": "GPT-4o — best quality, multimodal"},
    {"id": "gpt-4o-mini",         "label": "GPT-4o Mini — fast & affordable"},
    {"id": "gpt-4-turbo",         "label": "GPT-4 Turbo — previous generation"},
    {"id": "gpt-3.5-turbo",       "label": "GPT-3.5 Turbo — fastest / cheapest"},
]

ANTHROPIC_MODELS: list[dict] = [
    {"id": "claude-opus-4-6",              "label": "Claude Opus 4.6 — highest quality"},
    {"id": "claude-sonnet-4-6",            "label": "Claude Sonnet 4.6 — balanced quality/speed"},
    {"id": "claude-haiku-4-5-20251001",    "label": "Claude Haiku 4.5 — fastest / most affordable"},
    {"id": "claude-opus-4-5-20250514",     "label": "Claude Opus 4.5 — previous generation"},
    {"id": "claude-sonnet-4-5-20250514",   "label": "Claude Sonnet 4.5 — previous generation"},
]


# ─── Ollama ───────────────────────────────────────────────────────────────────

def get_ollama_host() -> str:
    """Return the Ollama host URL from env, defaulting to localhost."""
    return os.environ.get("POLYPHONY_OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def list_ollama_models(timeout: float = 3.0) -> list[str]:
    """Query the local Ollama instance and return installed model names.

    Returns an empty list if Ollama is not running or unreachable.
    """
    host = get_ollama_host()
    url = f"{host}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []


def ollama_is_running(timeout: float = 2.0) -> bool:
    """Return True if the local Ollama instance is reachable."""
    host = get_ollama_host()
    try:
        with urllib.request.urlopen(f"{host}/", timeout=timeout):
            return True
    except Exception:
        return False


# ─── Provider helpers ─────────────────────────────────────────────────────────

def default_model(provider: str) -> str:
    """Return a sensible default model name for the given provider."""
    defaults = {
        "ollama":    "llama3.1:8b",
        "openai":    "gpt-4o",
        "anthropic": "claude-sonnet-4-6",
    }
    return defaults.get(provider, "")


def model_options_for_provider(provider: str) -> list[str]:
    """Return ordered list of model IDs for the given provider.

    For Ollama this queries the live API; returns an empty list on failure.
    For cloud providers this returns the curated catalog.
    """
    if provider == "ollama":
        return list_ollama_models()
    if provider == "openai":
        return [m["id"] for m in OPENAI_MODELS]
    if provider == "anthropic":
        return [m["id"] for m in ANTHROPIC_MODELS]
    return []


def model_label(provider: str, model_id: str) -> str:
    """Return a human-readable label for a model, or just the ID if unknown."""
    catalog: list[dict] = []
    if provider == "openai":
        catalog = OPENAI_MODELS
    elif provider == "anthropic":
        catalog = ANTHROPIC_MODELS
    for entry in catalog:
        if entry["id"] == model_id:
            return entry["label"]
    return model_id


# ─── API key status ───────────────────────────────────────────────────────────

def check_api_keys() -> dict[str, Optional[str]]:
    """Return a dict of provider → masked key (or None if not set)."""
    def _mask(key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        if len(key) <= 8:
            return "****"
        return key[:4] + "…" + key[-4:]

    return {
        "openai":    _mask(os.environ.get("OPENAI_API_KEY")),
        "anthropic": _mask(os.environ.get("ANTHROPIC_API_KEY")),
    }
