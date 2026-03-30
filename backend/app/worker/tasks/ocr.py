from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import Document, DocumentPage, PageProcessingStatus, ParseStatus, TextSource
from ...services.ocr import OCRUnavailableError, ocr_pdf_page
from ._helpers import update_parse_status


def ocr_scanned_pages(document_id: str) -> dict[str, object]:
    update_parse_status(document_id, ParseStatus.ocr)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": str(document.id), "status": "skipped", "reason": "parse_failed"}

        if document.mime_type != "application/pdf":
            return {"document_id": str(document.id), "status": "skipped", "reason": "non_pdf"}

        pages = (
            db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )

        raw_settings = getattr(settings, "raw", {}) or {}
        min_raw_chars = int(raw_settings.get("ocr", {}).get("min_raw_text_chars", 50))
        scanned_pages = [
            page
            for page in pages
            if len((page.raw_text or "").strip()) < min_raw_chars or page.processing_status == PageProcessingStatus.failed
        ]
        attempted_page_count = len(scanned_pages)
        ocr_success_count = 0
        ocr_failed_count = 0

        for page in scanned_pages:
            try:
                ocr_text = ocr_pdf_page(document.file_path, page.page_number)
                page.raw_text = ocr_text
                page.text_source = TextSource.ocr
                page.processing_status = PageProcessingStatus.pending
                page.processing_error = None
                db.add(page)
                db.commit()
                ocr_success_count += 1
            except (OCRUnavailableError, Exception) as exc:
                db.rollback()
                page.processing_status = PageProcessingStatus.failed
                page.processing_error = f"ocr_failed: {exc}"[:1000]
                db.add(page)
                db.commit()
                ocr_failed_count += 1

        # MVP placeholder for OCR overlay artifact: store a processed copy path.
        source = Path(document.file_path)
        processed_dir = Path(settings.data_dir) / "processed" / str(document.id)
        processed_dir.mkdir(parents=True, exist_ok=True)
        processed_path = processed_dir / source.name
        if source.exists():
            processed_path.write_bytes(source.read_bytes())
            document.processed_file_path = str(processed_path)
            db.add(document)
            db.commit()
        return {
            "document_id": str(document.id),
            "status": "ok" if ocr_failed_count == 0 else "partial",
            "attempted_page_count": attempted_page_count,
            "ocr_success_count": ocr_success_count,
            "ocr_failed_count": ocr_failed_count,
            "processed_file_path": document.processed_file_path,
        }
    finally:
        db.close()
