from __future__ import annotations

import json
import logging

import litellm

logger = logging.getLogger(__name__)


class LLMResponseError(RuntimeError):
    """Raised when an LLM response cannot be parsed into the expected shape."""


def llm_completion(model: str, prompt: str) -> str:
    """Call LiteLLM and return the raw content string."""
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise LLMResponseError("LLM returned empty content")
    return content.strip()


def parse_json_dict(raw: str) -> dict:
    """Parse a JSON string and verify it is a dict."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMResponseError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMResponseError(f"Expected JSON object, got {type(data).__name__}")
    return data


def parse_json_list(raw: str) -> list[dict]:
    """Parse a JSON string into a list of dicts.

    Handles both raw arrays and ``{"items": [...]}`` wrapper objects.
    Non-dict entries are silently filtered.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMResponseError(f"Invalid JSON: {exc}") from exc

    if isinstance(data, dict):
        # Unwrap common wrapper keys
        for key in ("items", "results", "obligations", "risks", "entities"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            raise LLMResponseError("Expected JSON array or object with list field")

    if not isinstance(data, list):
        raise LLMResponseError(f"Expected JSON array, got {type(data).__name__}")

    return [entry for entry in data if isinstance(entry, dict)]


def classify(model: str, prompt: str) -> dict:
    """Run classification LLM call. Matches ``call_classification_llm`` signature."""
    raw = llm_completion(model, prompt)
    return parse_json_dict(raw)


def extract(model: str, prompt: str, stage: str) -> list[dict]:
    """Run extraction LLM call. Matches ``call_extract_llm`` signature."""
    raw = llm_completion(model, prompt)
    return parse_json_list(raw)
