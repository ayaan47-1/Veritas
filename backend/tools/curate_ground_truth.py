"""Auto-curate a generated ground_truth.json.

The GT generator (generate_ground_truth.py) can still pick up statutory
restatements from appended disclosure sections, even with the prompt update.
This tool takes a user-supplied list of known-statutory page ranges plus a
library of statutory-language regex patterns, then filters out any item that
matches either rule.

The original file is backed up to `ground_truth_original.json` on first run
(so re-running this is idempotent against the baseline, not the curated
version).

Usage:
    python3 -m backend.tools.curate_ground_truth \\
        --document-id <uuid> \\
        --statutory-pages "1-4,14-42"
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.tools.verify_section_filter import parse_page_ranges

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# Regex patterns that flag statutory restatements. Case-insensitive.
_STATUTORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("landlord_must", re.compile(r"^\s*A\s+landlord\s+(must|may|shall)", re.IGNORECASE)),
    ("landlords_required", re.compile(r"^\s*Landlord(s)?\s+(must|are\s+required)", re.IGNORECASE)),
    ("tenant_must", re.compile(r"^\s*A\s+tenant\s+(must|may|shall)", re.IGNORECASE)),
    ("utility_required", re.compile(r"^\s*Utility\s+companies\s+are\s+required", re.IGNORECASE)),
    ("under_code", re.compile(r"Under\s+\[?(?:Section|Code|§)", re.IGNORECASE)),
    ("tenant_has_right", re.compile(r"The\s+tenant\s+has\s+the\s+right\s+to", re.IGNORECASE)),
    ("illinois_law", re.compile(r"Illinois\s+law\s+requires", re.IGNORECASE)),
    ("rlto_reference", re.compile(r"(under\s+the\s+RLTO|RLTO\s+summary)", re.IGNORECASE)),
]


def _match_statutory_pattern(quote: str) -> str | None:
    """Return the name of the first matching pattern, or None."""
    if not quote:
        return None
    for name, pattern in _STATUTORY_PATTERNS:
        if pattern.search(quote):
            return name
    return None


def _filter_items(
    items: list[dict],
    statutory_pages: set[int],
    *,
    has_page_hint: bool,
) -> tuple[list[dict], list[dict], dict[str, int]]:
    """Return (kept, removed, removed_by_reason_counts)."""
    kept: list[dict] = []
    removed: list[dict] = []
    counts: dict[str, int] = {"page_range": 0}
    for name, _ in _STATUTORY_PATTERNS:
        counts[f"pattern:{name}"] = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "")

        # Rule 1: page_hint in statutory range (obligations only)
        if has_page_hint:
            page_hint = item.get("page_hint")
            if isinstance(page_hint, int) and page_hint in statutory_pages:
                removed.append({**item, "_removed_reason": "page_range"})
                counts["page_range"] += 1
                continue

        # Rule 2: statutory text pattern
        matched = _match_statutory_pattern(quote)
        if matched:
            removed.append({**item, "_removed_reason": f"pattern:{matched}"})
            counts[f"pattern:{matched}"] += 1
            continue

        kept.append(item)

    return kept, removed, counts


def curate(document_id: str, statutory_pages: set[int], gt_dir: Path) -> dict:
    doc_id = uuid.UUID(document_id)
    gt_path = gt_dir / str(doc_id) / "ground_truth.json"
    if not gt_path.exists():
        raise FileNotFoundError(
            f"Ground truth not found at {gt_path}. Run generate_ground_truth.py first."
        )

    backup_path = gt_path.parent / "ground_truth_original.json"
    if not backup_path.exists():
        shutil.copy2(gt_path, backup_path)
        logger.info("Backed up original to %s", backup_path)
    else:
        logger.info("Backup already exists at %s (using it as source of truth)", backup_path)

    # Always read from the backup so repeated curation is idempotent
    gt = json.loads(backup_path.read_text())

    ob_items = gt.get("obligations") or []
    ri_items = gt.get("risks") or []

    ob_kept, ob_removed, ob_counts = _filter_items(
        ob_items, statutory_pages, has_page_hint=True,
    )
    ri_kept, ri_removed, ri_counts = _filter_items(
        ri_items, statutory_pages, has_page_hint=False,
    )

    curated = {
        **gt,
        "obligations": ob_kept,
        "risks": ri_kept,
        "curation": {
            "statutory_pages": sorted(statutory_pages),
            "obligations_removed": len(ob_removed),
            "risks_removed": len(ri_removed),
            "obligations_removed_by_reason": ob_counts,
            "risks_removed_by_reason": ri_counts,
        },
    }
    gt_path.write_text(json.dumps(curated, indent=2))

    print("=" * 70)
    print(f"GROUND TRUTH CURATION — {document_id}")
    print(f"Statutory pages: {sorted(statutory_pages)}")
    print("=" * 70)
    print()
    print("OBLIGATIONS")
    print(f"  Before : {len(ob_items)}")
    print(f"  Kept   : {len(ob_kept)}")
    print(f"  Removed: {len(ob_removed)}")
    for reason, count in ob_counts.items():
        if count > 0:
            print(f"    - {reason:25s}: {count}")
    print()
    print("RISKS")
    print(f"  Before : {len(ri_items)}")
    print(f"  Kept   : {len(ri_kept)}")
    print(f"  Removed: {len(ri_removed)}")
    for reason, count in ri_counts.items():
        if count > 0:
            print(f"    - {reason:25s}: {count}")
    print()
    print(f"Backup: {backup_path}")
    print(f"Curated: {gt_path}")
    print("=" * 70)

    return {
        "document_id": document_id,
        "obligations_before": len(ob_items),
        "obligations_kept": len(ob_kept),
        "obligations_removed": len(ob_removed),
        "risks_before": len(ri_items),
        "risks_kept": len(ri_kept),
        "risks_removed": len(ri_removed),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-curate ground_truth.json by page ranges and statutory text patterns")
    parser.add_argument("--document-id", required=True)
    parser.add_argument(
        "--statutory-pages",
        required=True,
        help="Comma-separated page spec of known-statutory pages, e.g. '1-4,14-42'",
    )
    parser.add_argument(
        "--gt-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "benchmarks"),
    )
    args = parser.parse_args()
    statutory_pages = parse_page_ranges(args.statutory_pages)
    curate(args.document_id, statutory_pages, Path(args.gt_dir))


if __name__ == "__main__":
    main()
