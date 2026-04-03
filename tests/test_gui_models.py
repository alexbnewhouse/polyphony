"""Tests for polyphony_gui.models — provider helpers and model discovery."""

from __future__ import annotations

from polyphony_gui.models import (
    ANTHROPIC_MODELS,
    OPENAI_MODELS,
    default_model,
    model_options_for_provider,
)


def test_openai_models_non_empty():
    assert len(OPENAI_MODELS) > 0
    assert all("id" in m and "label" in m for m in OPENAI_MODELS)


def test_anthropic_models_non_empty():
    assert len(ANTHROPIC_MODELS) > 0
    assert all("id" in m and "label" in m for m in ANTHROPIC_MODELS)


def test_default_model_known_providers():
    assert default_model("ollama")
    assert default_model("openai")
    assert default_model("anthropic")


def test_default_model_unknown_provider():
    assert default_model("unknown") == ""


def test_model_options_openai():
    opts = model_options_for_provider("openai")
    assert len(opts) > 0
    assert "gpt-4o" in opts


def test_model_options_anthropic():
    opts = model_options_for_provider("anthropic")
    assert len(opts) > 0


def test_model_options_unknown():
    assert model_options_for_provider("unknown") == []
