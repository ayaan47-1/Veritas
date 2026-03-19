from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import Chunk, Document, DocumentPage, PageProcessingStatus, ParseStatus, SplitReason
from ...services.chunking import split_text_into_chunks
from ...services.normalization import normalize_text
from ._helpers import update_parse_status


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_pages(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.chunking)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed:
            return

        pages = (
            db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )

        for page in pages:
            if page.processing_status == PageProcessingStatus.failed:
                continue

            try:
                normalized = normalize_text(page.raw_text)
                page.normalized_text = normalized
                page.text_sha256 = _sha256(normalized)
                page.processing_status = PageProcessingStatus.processed
                page.processing_error = None
                db.add(page)
                db.commit()
            except Exception as exc:
                db.rollback()
                page.processing_status = PageProcessingStatus.failed
                page.processing_error = f"normalize_failed: {exc}"[:1000]
                db.add(page)
                db.commit()
    finally:
        db.close()


def chunk_pages(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.chunking)

    max_chars = int(settings.raw.get("chunking", {}).get("max_chars", 4000))

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed:
            return

        db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
        db.commit()

        pages = (
            db.query(DocumentPage)
            .filter(
                DocumentPage.document_id == document.id,
                DocumentPage.processing_status == PageProcessingStatus.processed,
            )
            .order_by(DocumentPage.page_number.asc())
            .all()
        )

        for page in pages:
            try:
                slices = split_text_into_chunks(page.normalized_text or "", max_chars=max_chars)
                for slc in slices:
                    if not slc.text.strip():
                        continue
                    db.add(
                        Chunk(
                            document_id=document.id,
                            page_number=page.page_number,
                            char_start=slc.char_start,
                            char_end=slc.char_end,
                            text=slc.text,
                            chunk_sha256=_sha256(slc.text),
                            split_reason=SplitReason(slc.split_reason),
                        )
                    )
                db.commit()
            except Exception as exc:
                db.rollback()
                page.processing_status = PageProcessingStatus.failed
                page.processing_error = f"chunk_failed: {exc}"[:1000]
                db.add(page)
                db.commit()
    finally:
        db.close()
