"""Tests for agent implementations and shared utilities."""

from __future__ import annotations

import json
import sqlite3
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from polyphony.agents.base import BaseAgent, parse_json
from polyphony.db import connect, fetchone, insert


# ──────────────────────────────────────────────────────────────────────────────
# parse_json utility
# ──────────────────────────────────────────────────────────────────────────────


class TestParseJson:
    """Tests for the shared parse_json function."""

    def test_clean_json(self):
        raw = '{"codes": ["A", "B"], "confidence": 0.9}'
        result = parse_json(raw)
        assert result == {"codes": ["A", "B"], "confidence": 0.9}

    def test_markdown_fenced_json(self):
        raw = 'Here is the result:\n```json\n{"codes": ["A"]}\n```\nDone.'
        result = parse_json(raw)
        assert result == {"codes": ["A"]}

    def test_markdown_fenced_no_language_tag(self):
        raw = 'Result:\n```\n{"codes": ["B"]}\n```'
        result = parse_json(raw)
        assert result == {"codes": ["B"]}

    def test_embedded_json_in_text(self):
        raw = 'I think the answer is {"codes": ["C"], "rationale": "test"} based on the text.'
        result = parse_json(raw)
        assert result == {"codes": ["C"], "rationale": "test"}

    def test_json_array(self):
        raw = '[{"name": "A"}, {"name": "B"}]'
        result = parse_json(raw)
        assert result == [{"name": "A"}, {"name": "B"}]

    def test_invalid_json_returns_empty_dict(self):
        raw = "This is not JSON at all."
        result = parse_json(raw)
        assert result == {}

    def test_empty_string_returns_empty_dict(self):
        assert parse_json("") == {}

    def test_nested_json(self):
        raw = '{"assignments": [{"code": "A", "confidence": 0.8}]}'
        result = parse_json(raw)
        assert result["assignments"][0]["code"] == "A"


# ──────────────────────────────────────────────────────────────────────────────
# OllamaAgent instantiation
# ──────────────────────────────────────────────────────────────────────────────


class TestOllamaAgent:
    """Test OllamaAgent can be instantiated with a mock client."""

    def test_instantiation_with_mock(self, conn, project_id):
        """OllamaAgent should instantiate when ollama package is available."""
        from polyphony.agents.ollama_agent import OllamaAgent
        from polyphony.db import fetchone

        with patch("polyphony.agents.ollama_agent._ollama") as mock_ollama:
            mock_client = MagicMock()
            mock_ollama.Client.return_value = mock_client
            # Mock the show call for digest
            mock_show = MagicMock()
            mock_show.modelinfo = {"general.file_type": "test_digest"}
            mock_client.show.return_value = mock_show

            # Use agent already created by the project_id fixture
            agent_row = fetchone(
                conn,
                "SELECT * FROM agent WHERE project_id = ? AND role = 'coder_a'",
                (project_id,),
            )

            agent = OllamaAgent(
                agent_id=agent_row["id"],
                project_id=project_id,
                role="coder_a",
                model_name=agent_row["model_name"],
                temperature=0.1,
                seed=42,
                conn=conn,
            )
            assert agent.model_name == agent_row["model_name"]
            assert agent.role == "coder_a"


# ──────────────────────────────────────────────────────────────────────────────
# OpenAIAgent
# ──────────────────────────────────────────────────────────────────────────────


class TestOpenAIAgent:
    """Test OpenAIAgent instantiation and error handling."""

    def test_missing_package_raises_import_error(self, conn, project_id):
        """Should raise ImportError when openai is not installed."""
        with patch("polyphony.agents.openai_agent._openai", None):
            from polyphony.agents.openai_agent import OpenAIAgent

            with pytest.raises(ImportError, match="openai"):
                OpenAIAgent(
                    agent_id=1,
                    project_id=project_id,
                    role="coder_a",
                    model_name="gpt-4o",
                    temperature=0.1,
                    seed=42,
                    conn=conn,
                    api_key="test-key",
                )

    def test_missing_api_key_raises_value_error(self, conn, project_id):
        """Should raise ValueError when no API key is provided."""
        mock_openai = MagicMock()
        with patch("polyphony.agents.openai_agent._openai", mock_openai), \
             patch.dict("os.environ", {}, clear=True):
            from polyphony.agents.openai_agent import OpenAIAgent

            with pytest.raises(ValueError, match="No OpenAI API key"):
                OpenAIAgent(
                    agent_id=1,
                    project_id=project_id,
                    role="coder_a",
                    model_name="gpt-4o",
                    temperature=0.1,
                    seed=42,
                    conn=conn,
                )

    def test_instantiation_with_explicit_key(self, conn, project_id):
        """Should instantiate when an explicit API key is provided."""
        mock_openai = MagicMock()
        with patch("polyphony.agents.openai_agent._openai", mock_openai):
            from polyphony.agents.openai_agent import OpenAIAgent

            agent = OpenAIAgent(
                agent_id=1,
                project_id=project_id,
                role="coder_a",
                model_name="gpt-4o",
                temperature=0.1,
                seed=42,
                conn=conn,
                api_key="sk-test-key",
            )
            assert agent.model_name == "gpt-4o"
            assert agent.role == "coder_a"
            mock_openai.OpenAI.assert_called_once_with(
                api_key="sk-test-key",
                base_url=None,
            )

    def test_env_var_api_key(self, conn, project_id):
        """Should pick up API key from POLYPHONY_OPENAI_API_KEY env var."""
        mock_openai = MagicMock()
        with patch("polyphony.agents.openai_agent._openai", mock_openai), \
             patch.dict("os.environ", {"POLYPHONY_OPENAI_API_KEY": "sk-env-key"}, clear=True):
            from polyphony.agents.openai_agent import OpenAIAgent

            agent = OpenAIAgent(
                agent_id=1,
                project_id=project_id,
                role="coder_a",
                model_name="gpt-4o",
                temperature=0.1,
                seed=42,
                conn=conn,
            )
            mock_openai.OpenAI.assert_called_once_with(
                api_key="sk-env-key",
                base_url=None,
            )


# ──────────────────────────────────────────────────────────────────────────────
# AnthropicAgent
# ──────────────────────────────────────────────────────────────────────────────


class TestAnthropicAgent:
    """Test AnthropicAgent instantiation and error handling."""

    def test_missing_package_raises_import_error(self, conn, project_id):
        """Should raise ImportError when anthropic is not installed."""
        with patch("polyphony.agents.anthropic_agent._anthropic", None):
            from polyphony.agents.anthropic_agent import AnthropicAgent

            with pytest.raises(ImportError, match="anthropic"):
                AnthropicAgent(
                    agent_id=1,
                    project_id=project_id,
                    role="coder_a",
                    model_name="claude-sonnet-4-5-20250514",
                    temperature=0.1,
                    seed=42,
                    conn=conn,
                    api_key="test-key",
                )

    def test_missing_api_key_raises_value_error(self, conn, project_id):
        """Should raise ValueError when no API key is provided."""
        mock_anthropic = MagicMock()
        with patch("polyphony.agents.anthropic_agent._anthropic", mock_anthropic), \
             patch.dict("os.environ", {}, clear=True):
            from polyphony.agents.anthropic_agent import AnthropicAgent

            with pytest.raises(ValueError, match="No Anthropic API key"):
                AnthropicAgent(
                    agent_id=1,
                    project_id=project_id,
                    role="coder_a",
                    model_name="claude-sonnet-4-5-20250514",
                    temperature=0.1,
                    seed=42,
                    conn=conn,
                )

    def test_instantiation_with_explicit_key(self, conn, project_id):
        """Should instantiate when an explicit API key is provided."""
        mock_anthropic = MagicMock()
        with patch("polyphony.agents.anthropic_agent._anthropic", mock_anthropic):
            from polyphony.agents.anthropic_agent import AnthropicAgent

            agent = AnthropicAgent(
                agent_id=1,
                project_id=project_id,
                role="coder_a",
                model_name="claude-sonnet-4-5-20250514",
                temperature=0.1,
                seed=42,
                conn=conn,
                api_key="sk-ant-test-key",
            )
            assert agent.model_name == "claude-sonnet-4-5-20250514"
            assert agent.role == "coder_a"
            mock_anthropic.Anthropic.assert_called_once_with(api_key="sk-ant-test-key")


# ──────────────────────────────────────────────────────────────────────────────
# build_agent_objects routing
# ──────────────────────────────────────────────────────────────────────────────


class TestBuildAgentObjects:
    """Test that build_agent_objects routes to the correct agent class."""

    def test_openai_agent_type_routes_correctly(self, conn, project_id):
        """agent_type='openai' should instantiate OpenAIAgent."""
        from polyphony.db import fetchall

        # Update coder_a to be openai type
        conn.execute(
            "UPDATE agent SET agent_type = 'openai', model_name = 'gpt-4o' "
            "WHERE project_id = ? AND role = 'coder_a'",
            (project_id,),
        )
        conn.commit()

        mock_openai = MagicMock()
        with patch("polyphony.agents.openai_agent._openai", mock_openai), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            from polyphony.utils import build_agent_objects
            from polyphony.agents.openai_agent import OpenAIAgent

            agent_a, agent_b, supervisor = build_agent_objects(conn, project_id)
            assert isinstance(agent_a, OpenAIAgent)

    def test_anthropic_agent_type_routes_correctly(self, conn, project_id):
        """agent_type='anthropic' should instantiate AnthropicAgent."""
        # Update coder_b to be anthropic type
        conn.execute(
            "UPDATE agent SET agent_type = 'anthropic', model_name = 'claude-sonnet-4-5-20250514' "
            "WHERE project_id = ? AND role = 'coder_b'",
            (project_id,),
        )
        conn.commit()

        mock_anthropic = MagicMock()
        with patch("polyphony.agents.anthropic_agent._anthropic", mock_anthropic), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=False):
            from polyphony.utils import build_agent_objects
            from polyphony.agents.anthropic_agent import AnthropicAgent

            agent_a, agent_b, supervisor = build_agent_objects(conn, project_id)
            assert isinstance(agent_b, AnthropicAgent)


# ──────────────────────────────────────────────────────────────────────────────
# BaseAgent call/logging behavior
# ──────────────────────────────────────────────────────────────────────────────


class _DummySuccessAgent(BaseAgent):
    def _call_llm(self, system_prompt, user_prompt, images=None):
        return "{\"ok\": true}", {"ok": True}


class _DummyFailAgent(BaseAgent):
    def _call_llm(self, system_prompt, user_prompt, images=None):
        raise RuntimeError("synthetic failure")


def _get_coder_a_id(conn, project_id):
    row = fetchone(
        conn,
        "SELECT id FROM agent WHERE project_id = ? AND role = 'coder_a'",
        (project_id,),
    )
    assert row is not None
    return row["id"]


def test_base_agent_call_logs_success_with_images(conn, project_id):
    agent = _DummySuccessAgent(
        agent_id=_get_coder_a_id(conn, project_id),
        project_id=project_id,
        role="coder_a",
        model_name="dummy",
        model_version="test",
        temperature=0.1,
        seed=42,
        conn=conn,
    )

    raw, parsed, call_id = agent.call(
        "coding",
        "SYSTEM",
        "USER",
        images=["/tmp/image1.png"],
    )

    assert raw == '{"ok": true}'
    assert parsed == {"ok": True}

    logged = fetchone(conn, "SELECT * FROM llm_call WHERE id = ?", (call_id,))
    assert logged is not None
    assert logged["error"] is None
    assert logged["parsed_output"] is not None
    assert "[Images: /tmp/image1.png]" in logged["user_prompt"]

    expected_hash = hashlib.sha256(
        ("SYSTEM\n---\nUSER\n\n[Images: /tmp/image1.png]").encode("utf-8")
    ).hexdigest()
    assert logged["prompt_hash"] == expected_hash


def test_base_agent_call_logs_errors_and_reraises(conn, project_id):
    agent = _DummyFailAgent(
        agent_id=_get_coder_a_id(conn, project_id),
        project_id=project_id,
        role="coder_a",
        model_name="dummy",
        model_version="test",
        temperature=0.1,
        seed=42,
        conn=conn,
    )

    with pytest.raises(RuntimeError, match="synthetic failure"):
        agent.call("coding", "SYSTEM", "USER")

    logged = fetchone(conn, "SELECT * FROM llm_call ORDER BY id DESC LIMIT 1")
    assert logged is not None
    assert "synthetic failure" in (logged["error"] or "")
    assert logged["parsed_output"] is None


def test_update_call_link_rejects_invalid_columns(conn, project_id):
    agent = _DummySuccessAgent(
        agent_id=_get_coder_a_id(conn, project_id),
        project_id=project_id,
        role="coder_a",
        model_name="dummy",
        model_version="test",
        temperature=0.1,
        seed=42,
        conn=conn,
    )

    _, _, call_id = agent.call("coding", "SYSTEM", "USER")
    with pytest.raises(ValueError, match="Invalid link column"):
        agent.update_call_link(call_id, not_a_real_fk=123)
