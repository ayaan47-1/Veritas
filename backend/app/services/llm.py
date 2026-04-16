from __future__ import annotations

import json
import logging
import re
from typing import Any

import litellm

logger = logging.getLogger(__name__)


class LLMResponseError(RuntimeError):
    """Raised when an LLM response cannot be parsed into the expected shape."""


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()
    return str(content).strip()


def llm_completion(model: str, prompt: str, *, prefer_json_object: bool = True) -> str:
    """Call LiteLLM and return the raw content string."""
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if prefer_json_object:
        kwargs["response_format"] = {"type": "json_object"}

    kwargs["timeout"] = 120  # seconds; prevents indefinite hangs on provider outages

    response = litellm.completion(**kwargs)
    content = response.choices[0].message.content
    normalized = _normalize_content(content)
    if not normalized:
        raise LLMResponseError("LLM returned empty content")
    return normalized


def _strip_code_fences(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _recover_json(raw: str) -> Any:
    cleaned = _strip_code_fences(raw)
    decoder = json.JSONDecoder()

    try:
        value, _idx = decoder.raw_decode(cleaned)
        return value
    except (json.JSONDecodeError, TypeError):
        pass

    for idx, char in enumerate(cleaned):
        if char not in ("{", "["):
            continue
        snippet = cleaned[idx:]
        try:
            value, _end = decoder.raw_decode(snippet)
            return value
        except (json.JSONDecodeError, TypeError):
            continue

    raise LLMResponseError("Invalid JSON: unable to recover JSON payload")


def parse_json_dict(raw: str) -> dict:
    """Parse a JSON string and verify it is a dict."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        try:
            data = _recover_json(raw)
        except LLMResponseError:
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
        try:
            data = _recover_json(raw)
        except LLMResponseError:
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
    raw = llm_completion(model, prompt, prefer_json_object=True)
    return parse_json_dict(raw)


def extract(model: str, prompt: str, stage: str) -> list[dict]:
    """Run extraction LLM call. Matches ``call_extract_llm`` signature."""
    raw = llm_completion(model, prompt, prefer_json_object=False)
    return parse_json_list(raw)
