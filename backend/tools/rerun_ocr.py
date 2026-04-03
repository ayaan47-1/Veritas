"""Re-run OCR stage (stage 2) on an already-processed document.

Useful for testing OCR config changes without re-uploading the file.
Clears existing processing_error on scanned pages before re-running.

Usage:
    python3 -m backend.tools.rerun_ocr --document-id <uuid>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.database import SessionLocal
from backend.app.models import DocumentPage, PageProcessingStatus
from backend.app.worker.tasks.ocr import ocr_scanned_pages

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    args = parser.parse_args()

    doc_id = args.document_id

    # Clear previous OCR errors so pages are retried
    db = SessionLocal()
    try:
        pages = db.query(DocumentPage).filter(
            DocumentPage.document_id == doc_id,
            DocumentPage.processing_error.ilike("ocr_failed%"),
        ).all()
        for page in pages:
            page.processing_error = None
            page.processing_status = PageProcessingStatus.failed
            db.add(page)
        db.commit()
        log.info("Cleared OCR errors on %d pages", len(pages))
    finally:
        db.close()

    result = ocr_scanned_pages(doc_id)
    log.info("OCR result: %s", result)


if __name__ == "__main__":
    main()
