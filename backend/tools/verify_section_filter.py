"""Verify section classifier output against known-good page ranges.

Use this when you know which pages of a document belong to the actual agreement
body (e.g. from a structured benchmark). It queries the chunks, groups them by
`section_label`, and reports any misclassifications in both directions:

- False exclusion: chunk on an expected-agreement page but labeled `non_agreement`
  (agreement content being filtered out).
- False inclusion: chunk on a non-agreement page but labeled `agreement_body`
  (statutory content leaking through into extraction).

Usage:
    python3 -m backend.tools.verify_section_filter \\
        --document-id <uuid> \\
        --agreement-pages "5-13,43"
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.database import SessionLocal
from backend.app.models import Chunk, Document

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_page_ranges(spec: str) -> set[int]:
    """Parse a page spec like "5-13,43" or "5" or "1-4,14-20,22-31" into a set."""
    if not spec or not spec.strip():
        raise ValueError("empty page spec")
    pages: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_str, end_str = token.split("-", 1)
            start = int(start_str.strip())
            end = int(end_str.strip())
            if start > end:
                raise ValueError(f"invalid range {token!r}: start > end")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    return pages


def verify(document_id: str, agreement_pages: set[int]) -> dict:
    doc_id = uuid.UUID(document_id)
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            raise ValueError(f"Document {doc_id} not found")

        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc_id)
            .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
            .all()
        )
    finally:
        db.close()

    total = len(chunks)
    count_agreement = 0
    count_non_agreement = 0
    count_null = 0

    false_exclusions: list[Chunk] = []  # agreement page, labeled non_agreement
    false_inclusions: list[Chunk] = []  # non-agreement page, labeled agreement_body

    for chunk in chunks:
        label = chunk.section_label
        if label == "agreement_body":
            count_agreement += 1
        elif label == "non_agreement":
            count_non_agreement += 1
        else:
            count_null += 1

        page = chunk.page_number
        on_agreement_page = page in agreement_pages
        if on_agreement_page and label == "non_agreement":
            false_exclusions.append(chunk)
        elif not on_agreement_page and label == "agreement_body":
            false_inclusions.append(chunk)

    print("=" * 70)
    print(f"SECTION FILTER VERIFICATION — {document_id}")
    print(f"Document: {document.source_name}  (type: {document.doc_type.value})")
    print(f"Expected agreement pages: {sorted(agreement_pages)}")
    print("=" * 70)
    print()
    print(f"Total chunks        : {total}")
    print(f"  agreement_body    : {count_agreement}")
    print(f"  non_agreement     : {count_non_agreement}")
    print(f"  unlabeled (null)  : {count_null}")
    print()
    print(f"False exclusions (agreement page → non_agreement): {len(false_exclusions)}")
    print(f"False inclusions (non-agreement page → agreement_body): {len(false_inclusions)}")
    print()

    if false_exclusions:
        print("FALSE EXCLUSIONS (agreement content filtered out):")
        for chunk in false_exclusions:
            preview = (chunk.text or "").strip().replace("\n", " ")[:100]
            print(f"  [page {chunk.page_number}] {preview}")
        print()

    if false_inclusions:
        print("FALSE INCLUSIONS (statutory content leaking through):")
        for chunk in false_inclusions:
            preview = (chunk.text or "").strip().replace("\n", " ")[:100]
            print(f"  [page {chunk.page_number}] {preview}")
        print()

    print("=" * 70)

    return {
        "document_id": document_id,
        "total": total,
        "agreement_body": count_agreement,
        "non_agreement": count_non_agreement,
        "unlabeled": count_null,
        "false_exclusions": len(false_exclusions),
        "false_inclusions": len(false_inclusions),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify section classifier output vs expected agreement pages")
    parser.add_argument("--document-id", required=True)
    parser.add_argument(
        "--agreement-pages",
        required=True,
        help="Comma-separated page spec, e.g. '5-13,43'",
    )
    args = parser.parse_args()
    agreement_pages = parse_page_ranges(args.agreement_pages)
    verify(args.document_id, agreement_pages)


if __name__ == "__main__":
    main()
