"""Tests for polyphony.generators — synthetic QDA data generation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from polyphony.generators import (
    DOMAINS,
    generate_llm_data,
    generate_template_data,
    get_domains,
)


# ---------------------------------------------------------------------------
# get_domains
# ---------------------------------------------------------------------------


class TestGetDomains:
    def test_returns_all_domains(self):
        domains = get_domains()
        assert "housing" in domains
        assert "healthcare" in domains
        assert "education" in domains

    def test_returns_descriptions(self):
        domains = get_domains()
        for key, desc in domains.items():
            assert isinstance(desc, str)
            assert len(desc) > 10


# ---------------------------------------------------------------------------
# Domain data quality
# ---------------------------------------------------------------------------


class TestDomainData:
    @pytest.mark.parametrize("domain", DOMAINS.keys())
    def test_has_enough_templates(self, domain):
        assert len(DOMAINS[domain]["templates"]) >= 40

    @pytest.mark.parametrize("domain", DOMAINS.keys())
    def test_has_diverse_names(self, domain):
        assert len(DOMAINS[domain]["names"]) >= 20

    @pytest.mark.parametrize("domain", DOMAINS.keys())
    def test_has_codes(self, domain):
        codes = DOMAINS[domain]["codes"]
        assert len(codes) >= 4
        for code in codes:
            assert "name" in code
            assert "description" in code
            assert "inclusion_criteria" in code

    @pytest.mark.parametrize("domain", DOMAINS.keys())
    def test_templates_are_realistic_length(self, domain):
        """Templates should be at least 50 chars (2+ sentences)."""
        for tpl in DOMAINS[domain]["templates"]:
            assert len(tpl) >= 50, f"Template too short: {tpl[:60]}..."


# ---------------------------------------------------------------------------
# generate_template_data
# ---------------------------------------------------------------------------


class TestGenerateTemplateData:
    def test_generates_correct_count(self):
        result = generate_template_data("housing", n_segments=15, seed=42)
        assert len(result["segments"]) == 15

    def test_returns_codes(self):
        result = generate_template_data("housing", n_segments=5, seed=42)
        assert len(result["codes"]) > 0
        assert result["codes"][0]["name"]

    def test_seed_reproducibility(self):
        r1 = generate_template_data("housing", n_segments=10, seed=123)
        r2 = generate_template_data("housing", n_segments=10, seed=123)
        texts1 = [s["text"] for s in r1["segments"]]
        texts2 = [s["text"] for s in r2["segments"]]
        assert texts1 == texts2

    def test_different_seeds_differ(self):
        r1 = generate_template_data("housing", n_segments=10, seed=1)
        r2 = generate_template_data("housing", n_segments=10, seed=2)
        texts1 = [s["text"] for s in r1["segments"]]
        texts2 = [s["text"] for s in r2["segments"]]
        assert texts1 != texts2

    def test_metadata_fields(self):
        result = generate_template_data("healthcare", n_segments=3, seed=42)
        seg = result["segments"][0]
        assert "metadata" in seg
        assert seg["metadata"]["generated"] is True
        assert seg["metadata"]["domain"] == "Healthcare Access"
        assert seg["metadata"]["participant"]

    def test_name_placeholder_replaced(self):
        result = generate_template_data("housing", n_segments=50, seed=42)
        for seg in result["segments"]:
            assert "{name}" not in seg["text"]

    def test_unknown_domain_raises(self):
        with pytest.raises(ValueError, match="Unknown domain"):
            generate_template_data("nonexistent")

    @pytest.mark.parametrize("domain", DOMAINS.keys())
    def test_all_domains_generate(self, domain):
        result = generate_template_data(domain, n_segments=5, seed=42)
        assert len(result["segments"]) == 5
        assert len(result["codes"]) > 0


# ---------------------------------------------------------------------------
# generate_llm_data (mocked Ollama)
# ---------------------------------------------------------------------------


class TestGenerateLlmData:
    def _mock_ollama_response(self, content: str):
        """Create a mock ollama response object."""
        mock_message = MagicMock()
        mock_message.content = content
        mock_response = MagicMock()
        mock_response.message = mock_message
        return mock_response

    def _make_mock_ollama(self, chat_return=None, chat_side_effect=None):
        """Create a mock ollama module with a mock Client."""
        mock_module = MagicMock()
        mock_client = MagicMock()
        if chat_side_effect:
            mock_client.chat.side_effect = chat_side_effect
        elif chat_return:
            mock_client.chat.return_value = chat_return
        mock_module.Client.return_value = mock_client
        return mock_module, mock_client

    def test_basic_generation(self):
        import json

        response_data = {
            "segments": [
                {"text": "I worry about the future constantly.", "participant": "Alex"},
                {"text": "The floods last year changed everything.", "participant": "Sam"},
            ],
            "codes": [
                {"name": "ANXIETY", "description": "Climate-related anxiety", "inclusion_criteria": "...", "exclusion_criteria": "..."},
            ],
        }
        mock_module, _ = self._make_mock_ollama(
            chat_return=self._mock_ollama_response(json.dumps(response_data))
        )

        with patch.dict("sys.modules", {"ollama": mock_module}):
            result = generate_llm_data(topic="climate anxiety", n_segments=2, seed=42)
        assert len(result["segments"]) == 2
        assert result["segments"][0]["text"] == "I worry about the future constantly."
        assert result["segments"][0]["metadata"]["generated"] is True
        assert len(result["codes"]) == 1

    def test_malformed_json_fallback(self):
        mock_module, _ = self._make_mock_ollama(
            chat_return=self._mock_ollama_response("not valid json at all")
        )

        with patch.dict("sys.modules", {"ollama": mock_module}):
            result = generate_llm_data(topic="test", n_segments=5)
        assert result["segments"] == []
        assert result["codes"] == []

    def test_string_segments_handled(self):
        import json

        response_data = {
            "segments": ["Just a plain string segment.", "Another one."],
            "codes": [],
        }
        mock_module, _ = self._make_mock_ollama(
            chat_return=self._mock_ollama_response(json.dumps(response_data))
        )

        with patch.dict("sys.modules", {"ollama": mock_module}):
            result = generate_llm_data(topic="test", n_segments=2)
        assert len(result["segments"]) == 2
        assert result["segments"][0]["text"] == "Just a plain string segment."

    def test_ollama_error_raises_runtime(self):
        mock_module, _ = self._make_mock_ollama(
            chat_side_effect=ConnectionError("connection refused")
        )

        with patch.dict("sys.modules", {"ollama": mock_module}):
            with pytest.raises(RuntimeError, match="Ollama call failed"):
                generate_llm_data(topic="test", n_segments=5)

    def test_seed_passed_to_ollama(self):
        import json

        mock_module, mock_client = self._make_mock_ollama(
            chat_return=self._mock_ollama_response(json.dumps({"segments": [], "codes": []}))
        )

        with patch.dict("sys.modules", {"ollama": mock_module}):
            generate_llm_data(topic="test", n_segments=5, seed=99)

        call_kwargs = mock_client.chat.call_args
        assert call_kwargs.kwargs["options"]["seed"] == 99
