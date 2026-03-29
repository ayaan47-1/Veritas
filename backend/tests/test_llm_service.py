"""Tests for backend.app.services.llm module."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.llm import (
    LLMResponseError,
    classify,
    extract,
    llm_completion,
    parse_json_dict,
    parse_json_list,
)


# ── parse_json_dict ──────────────────────────────────────────────────


class TestParseJsonDict:
    def test_valid_dict(self):
        assert parse_json_dict('{"doc_type": "contract", "confidence": 0.9}') == {
            "doc_type": "contract",
            "confidence": 0.9,
        }

    def test_rejects_list(self):
        with pytest.raises(LLMResponseError, match="Expected JSON object"):
            parse_json_dict('[{"a": 1}]')

    def test_rejects_invalid_json(self):
        with pytest.raises(LLMResponseError, match="Invalid JSON"):
            parse_json_dict("not json at all")

    def test_rejects_scalar(self):
        with pytest.raises(LLMResponseError, match="Expected JSON object"):
            parse_json_dict('"just a string"')

    def test_recovers_json_from_code_fence(self):
        raw = """```json
{"doc_type":"contract","confidence":0.92}
```"""
        assert parse_json_dict(raw) == {"doc_type": "contract", "confidence": 0.92}


# ── parse_json_list ──────────────────────────────────────────────────


class TestParseJsonList:
    def test_valid_array(self):
        result = parse_json_list('[{"name": "Alice"}, {"name": "Bob"}]')
        assert result == [{"name": "Alice"}, {"name": "Bob"}]

    def test_unwrap_items_key(self):
        raw = json.dumps({"items": [{"x": 1}, {"x": 2}]})
        assert parse_json_list(raw) == [{"x": 1}, {"x": 2}]

    def test_unwrap_obligations_key(self):
        raw = json.dumps({"obligations": [{"q": "must"}]})
        assert parse_json_list(raw) == [{"q": "must"}]

    def test_filters_non_dicts(self):
        raw = json.dumps([{"a": 1}, "stray string", 42, {"b": 2}])
        assert parse_json_list(raw) == [{"a": 1}, {"b": 2}]

    def test_rejects_scalar(self):
        with pytest.raises(LLMResponseError, match="Expected JSON array"):
            parse_json_list("42")

    def test_rejects_dict_without_list_field(self):
        with pytest.raises(LLMResponseError, match="Expected JSON array or object"):
            parse_json_list('{"foo": "bar"}')

    def test_rejects_invalid_json(self):
        with pytest.raises(LLMResponseError, match="Invalid JSON"):
            parse_json_list("{bad json")

    def test_recovers_array_from_wrapped_text(self):
        raw = "Model output:\\n```json\\n[{\"quote\":\"shall pay\"}]\\n```\\nThanks."
        assert parse_json_list(raw) == [{"quote": "shall pay"}]


# ── Integration tests (mock litellm.completion) ─────────────────────


def _fake_response(content: str):
    """Build a minimal object mimicking litellm.completion() return."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


class TestClassifyIntegration:
    @patch("backend.app.services.llm.litellm")
    def test_classify_round_trip(self, mock_litellm):
        mock_litellm.completion.return_value = _fake_response(
            '{"doc_type": "invoice", "confidence": 0.85}'
        )
        result = classify(model="gpt-4o", prompt="classify this")
        assert result == {"doc_type": "invoice", "confidence": 0.85}
        mock_litellm.completion.assert_called_once()


class TestExtractIntegration:
    @patch("backend.app.services.llm.litellm")
    def test_extract_round_trip(self, mock_litellm):
        mock_litellm.completion.return_value = _fake_response(
            '[{"quote": "shall deliver", "obligation_type": "delivery"}]'
        )
        result = extract(model="gpt-4o", prompt="extract obligations", stage="obligation_extraction")
        assert result == [{"quote": "shall deliver", "obligation_type": "delivery"}]
        mock_litellm.completion.assert_called_once()


class TestLlmCompletionErrors:
    @patch("backend.app.services.llm.litellm")
    def test_empty_content_raises(self, mock_litellm):
        mock_litellm.completion.return_value = _fake_response("")
        with pytest.raises(LLMResponseError, match="empty content"):
            llm_completion(model="gpt-4o", prompt="test")

    @patch("backend.app.services.llm.litellm")
    def test_api_error_propagates(self, mock_litellm):
        mock_litellm.completion.side_effect = Exception("API quota exceeded")
        with pytest.raises(Exception, match="API quota exceeded"):
            llm_completion(model="gpt-4o", prompt="test")
