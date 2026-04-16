"""Tests for the LLM-as-judge audit tool."""
from __future__ import annotations

from backend.tools import audit_extractions as audit


def test_chunked_splits_evenly():
    assert audit._chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunked_larger_than_list():
    assert audit._chunked([1, 2], 10) == [[1, 2]]


def test_chunked_empty():
    assert audit._chunked([], 5) == []


def test_format_candidates_numbers_items():
    items = [{"quote": "First item."}, {"quote": "Second item."}]
    out = audit._format_candidates(items, "quote")
    assert "[0] First item." in out
    assert "[1] Second item." in out


def test_adjusted_metrics_accepts_fp_as_tp():
    # Original: 10 TP, 20 FP, 5 FN → precision = 10/30 = 0.333
    # If judge accepts 15 of 20 FP → adjusted TP = 25, rejected FP = 5
    # Adjusted precision = 25/30 = 0.833, recall = 25/30 = 0.833
    result = audit._adjusted_metrics(tp=10, accepted_fp=15, rejected_fp=5, fn=5)
    assert result["precision"] == 0.833
    assert result["recall"] == 0.833
    assert result["f1"] == 0.833


def test_adjusted_metrics_zero_division_safe():
    result = audit._adjusted_metrics(tp=0, accepted_fp=0, rejected_fp=0, fn=0)
    assert result["precision"] == 0.0
    assert result["recall"] == 0.0
    assert result["f1"] == 0.0


def test_judge_batch_handles_empty_input():
    # Monkeypatch would normally be needed, but empty input shouldn't call LLM
    result = audit._judge_batch([], "quote", "obligations", "lease", "claude-sonnet-4-6")
    assert result == []


def test_judge_batch_defaults_missing_verdicts_to_reject(monkeypatch):
    items = [{"quote": "First"}, {"quote": "Second"}, {"quote": "Third"}]
    # Judge only returns verdict for index 1, forgets 0 and 2
    fake_response = [{"index": 1, "verdict": "accept", "reason": "legitimate"}]

    monkeypatch.setattr(audit, "llm_completion", lambda *a, **k: "stub")
    monkeypatch.setattr(audit, "parse_json_list", lambda _raw: fake_response)

    result = audit._judge_batch(items, "quote", "obligations", "lease", "model")
    assert len(result) == 3
    assert result[0]["verdict"] == "reject"  # default for missing
    assert result[1]["verdict"] == "accept"
    assert result[2]["verdict"] == "reject"
