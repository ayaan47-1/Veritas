"""Tests for verify_section_filter and curate_ground_truth."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tools.verify_section_filter import parse_page_ranges
from backend.tools.curate_ground_truth import (
    _filter_items,
    _match_statutory_pattern,
    curate,
)


# ── parse_page_ranges ────────────────────────────────────────────────────────


def test_parse_page_ranges_single_range():
    assert parse_page_ranges("5-13,43") == {5, 6, 7, 8, 9, 10, 11, 12, 13, 43}


def test_parse_page_ranges_single_page():
    assert parse_page_ranges("7") == {7}


def test_parse_page_ranges_multiple_ranges():
    result = parse_page_ranges("1-4,14-20,22-31")
    assert result == set(range(1, 5)) | set(range(14, 21)) | set(range(22, 32))


def test_parse_page_ranges_with_whitespace():
    assert parse_page_ranges("5 - 7 , 10") == {5, 6, 7, 10}


def test_parse_page_ranges_rejects_empty():
    with pytest.raises(ValueError):
        parse_page_ranges("")


def test_parse_page_ranges_rejects_reverse_range():
    with pytest.raises(ValueError):
        parse_page_ranges("10-5")


# ── _match_statutory_pattern ─────────────────────────────────────────────────


def test_match_landlord_must():
    assert _match_statutory_pattern("A landlord must return the security deposit") == "landlord_must"


def test_match_landlords_required():
    assert _match_statutory_pattern("Landlords are required under Illinois law to") == "landlords_required"


def test_match_tenant_must():
    assert _match_statutory_pattern("A tenant must provide written notice") == "tenant_must"


def test_match_utility_required():
    assert _match_statutory_pattern("Utility companies are required to defer") == "utility_required"


def test_match_under_code():
    assert _match_statutory_pattern("Under Section 5-12-080 of the Code") == "under_code"


def test_match_tenant_has_right():
    assert _match_statutory_pattern("The tenant has the right to terminate") == "tenant_has_right"


def test_no_match_for_contractual_language():
    assert _match_statutory_pattern("Lessee shall pay rent on the first of the month") is None


def test_no_match_for_empty():
    assert _match_statutory_pattern("") is None


# ── _filter_items ────────────────────────────────────────────────────────────


def test_filter_obligations_drops_statutory_page():
    items = [
        {"quote": "Lessee shall pay rent.", "page_hint": 6},
        {"quote": "Something here", "page_hint": 3},  # in statutory range
        {"quote": "Another clause", "page_hint": 43},
    ]
    statutory = {1, 2, 3, 4}
    kept, removed, counts = _filter_items(items, statutory, has_page_hint=True)
    assert len(kept) == 2
    assert len(removed) == 1
    assert counts["page_range"] == 1
    assert removed[0]["page_hint"] == 3


def test_filter_obligations_drops_statutory_text():
    items = [
        {"quote": "Lessee shall pay rent.", "page_hint": 5},
        {"quote": "A landlord must return the deposit within 30 days.", "page_hint": 5},
        {"quote": "Tenant agrees to maintain the premises.", "page_hint": 6},
    ]
    statutory: set[int] = set()
    kept, removed, counts = _filter_items(items, statutory, has_page_hint=True)
    assert len(kept) == 2
    assert len(removed) == 1
    assert counts["pattern:landlord_must"] == 1


def test_filter_risks_skips_page_rule_but_applies_text_rule():
    items = [
        {"quote": "Lessee is liable for damages."},
        {"quote": "The tenant has the right to terminate."},
    ]
    # Risks have no page_hint, so has_page_hint=False
    kept, removed, counts = _filter_items(items, set(), has_page_hint=False)
    assert len(kept) == 1
    assert len(removed) == 1
    assert counts["pattern:tenant_has_right"] == 1


def test_filter_preserves_unmatched_items():
    items = [
        {"quote": "Lessee shall pay rent.", "page_hint": 5},
        {"quote": "Tenant agrees to keep premises clean.", "page_hint": 7},
    ]
    kept, removed, _ = _filter_items(items, {1, 2}, has_page_hint=True)
    assert len(kept) == 2
    assert len(removed) == 0


# ── curate (end-to-end on synthetic JSON) ────────────────────────────────────


def test_curate_end_to_end_filters_and_backs_up(tmp_path: Path):
    doc_id = "00000000-0000-0000-0000-000000000001"
    gt_dir = tmp_path / "benchmarks"
    doc_dir = gt_dir / doc_id
    doc_dir.mkdir(parents=True)

    gt_data = {
        "document_id": doc_id,
        "obligations": [
            {"quote": "Lessee shall pay rent.", "page_hint": 6, "severity": "high"},
            {"quote": "A landlord must return deposit.", "page_hint": 5, "severity": "medium"},
            {"quote": "Random text on page 3.", "page_hint": 3, "severity": "low"},
        ],
        "risks": [
            {"quote": "Late payment triggers penalty.", "severity": "high"},
            {"quote": "Under Section 5-12 the tenant may sue.", "severity": "critical"},
        ],
    }
    gt_path = doc_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(gt_data, indent=2))

    result = curate(doc_id, statutory_pages={1, 2, 3, 4}, gt_dir=gt_dir)

    # Original backed up
    backup = doc_dir / "ground_truth_original.json"
    assert backup.exists()
    backup_data = json.loads(backup.read_text())
    assert len(backup_data["obligations"]) == 3

    # Curated file written
    curated = json.loads(gt_path.read_text())
    # Lessee shall pay rent → kept; A landlord must → removed (pattern); Random page 3 → removed (page range)
    assert len(curated["obligations"]) == 1
    # Late payment penalty → kept; Under Section → removed (pattern)
    assert len(curated["risks"]) == 1

    assert result["obligations_kept"] == 1
    assert result["obligations_removed"] == 2
    assert result["risks_kept"] == 1
    assert result["risks_removed"] == 1


def test_curate_is_idempotent_against_backup(tmp_path: Path):
    """Re-running uses the backup as the source, not the already-curated file."""
    doc_id = "00000000-0000-0000-0000-000000000002"
    gt_dir = tmp_path / "benchmarks"
    doc_dir = gt_dir / doc_id
    doc_dir.mkdir(parents=True)

    gt_data = {
        "obligations": [
            {"quote": "Lessee shall pay rent.", "page_hint": 6},
            {"quote": "A landlord must return deposit.", "page_hint": 5},
        ],
        "risks": [],
    }
    gt_path = doc_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(gt_data, indent=2))

    # First curation
    curate(doc_id, statutory_pages=set(), gt_dir=gt_dir)
    first_curated = json.loads(gt_path.read_text())

    # Second curation should produce the same result (uses backup)
    curate(doc_id, statutory_pages=set(), gt_dir=gt_dir)
    second_curated = json.loads(gt_path.read_text())

    assert first_curated["obligations"] == second_curated["obligations"]
    assert len(first_curated["obligations"]) == 1  # landlord_must filtered
