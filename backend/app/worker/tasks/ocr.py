from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import Document, DocumentPage, PageProcessingStatus, ParseStatus, TextSource
from ...services.ocr import OCRUnavailableError, ocr_pdf_page
from ._helpers import update_parse_status


def ocr_scanned_pages(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.ocr)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed:
            return

        if (document.scanned_page_count or 0) <= 0:
            return

        pages = (
            db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )

        scanned_pages = [
            page
            for page in pages
            if page.processing_status != PageProcessingStatus.failed and len((page.raw_text or "").strip()) < 50
        ]

        for page in scanned_pages:
            try:
                ocr_text = ocr_pdf_page(document.file_path, page.page_number)
                page.raw_text = ocr_text
                page.text_source = TextSource.ocr
                page.processing_error = None
                db.add(page)
                db.commit()
            except (OCRUnavailableError, Exception) as exc:
                db.rollback()
                page.processing_status = PageProcessingStatus.failed
                page.processing_error = f"ocr_failed: {exc}"[:1000]
                db.add(page)
                db.commit()

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
    finally:
        db.close()
