from __future__ import annotations

import hashlib
from pathlib import Path
import uuid

import fitz
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models import Chunk, Document, DocumentPage, PageProcessingStatus, ParseStatus, TextSource, TextSpan
from ..celery_app import celery_app
from ._helpers import update_parse_status


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _looks_like_table(raw_text: str) -> bool:
    lines = [line for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return False

    delimiter_lines = sum(1 for line in lines if ("|" in line or "\t" in line))
    spaced_columns = sum(1 for line in lines if "  " in line and len(line.split()) >= 3)
    return delimiter_lines >= 2 or spaced_columns >= 3


def _locate_span(raw_text: str, span_text: str, from_pos: int) -> tuple[int, int, int] | None:
    text = (span_text or "").strip()
    if not text:
        return None

    idx = raw_text.find(text, from_pos)
    if idx < 0:
        idx = raw_text.find(text)
    if idx < 0:
        return None

    end = idx + len(text)
    return idx, end, end


def _extract_text_spans(page: fitz.Page, raw_text: str) -> list[dict]:
    spans: list[dict] = []
    page_dict = page.get_text("dict")
    cursor = 0

    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = str(span.get("text", ""))
                located = _locate_span(raw_text, span_text, cursor)
                if not located:
                    continue
                char_start, char_end, cursor = located
                bbox = span.get("bbox", [0.0, 0.0, 0.0, 0.0])
                spans.append(
                    {
                        "char_start": char_start,
                        "char_end": char_end,
                        "bbox_x1": float(bbox[0]),
                        "bbox_y1": float(bbox[1]),
                        "bbox_x2": float(bbox[2]),
                        "bbox_y2": float(bbox[3]),
                        "span_text": span_text,
                        "span_sha256": _sha256(span_text),
                    }
                )

    return spans


def _persist_failed_page(db: Session, document_id: uuid.UUID, page_number: int, error: str) -> None:
    failed_page = DocumentPage(
        document_id=document_id,
        page_number=page_number,
        raw_text="",
        normalized_text="",
        text_source=TextSource.pdf_text,
        text_sha256=_sha256(""),
        has_tables=False,
        processing_status=PageProcessingStatus.failed,
        processing_error=error[:1000],
    )
    db.add(failed_page)
    db.commit()


@celery_app.task(name="parse_document")
def parse_document(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.parsing)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return

        db.query(TextSpan).filter(TextSpan.document_id == document.id).delete(synchronize_session=False)
        db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
        db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete(synchronize_session=False)
        db.commit()

        ext = Path(document.source_name).suffix.lower()
        if ext == ".txt" or document.mime_type == "text/plain":
            try:
                raw_text = Path(document.file_path).read_text(encoding="utf-8", errors="replace")
                page = DocumentPage(
                    document_id=document.id,
                    page_number=1,
                    raw_text=raw_text,
                    normalized_text="",
                    text_source=TextSource.pdf_text,
                    text_sha256=_sha256(raw_text),
                    has_tables=_looks_like_table(raw_text),
                    processing_status=PageProcessingStatus.pending,
                )
                db.add(page)
                document.total_pages = 1
                document.scanned_page_count = 0
                db.add(document)
                db.commit()
            except Exception as exc:
                db.rollback()
                _persist_failed_page(db, document.id, 1, f"txt_parse_failed: {exc}")
                document.total_pages = 1
                document.scanned_page_count = 0
                db.add(document)
                db.commit()
            return

        scanned_count = 0
        try:
            pdf = fitz.open(document.file_path)
        except Exception as exc:
            document.parse_status = ParseStatus.failed
            document.notes = f"parse_open_failed: {exc}"
            db.add(document)
            db.commit()
            return

        with pdf:
            total_pages = pdf.page_count
            document.total_pages = total_pages
            db.add(document)
            db.commit()

            for page_idx in range(total_pages):
                page_number = page_idx + 1
                try:
                    page = pdf.load_page(page_idx)
                    raw_text = page.get_text("text") or ""
                    is_scanned = len(raw_text.strip()) < 50
                    if is_scanned:
                        scanned_count += 1

                    page_record = DocumentPage(
                        document_id=document.id,
                        page_number=page_number,
                        raw_text=raw_text,
                        normalized_text="",
                        text_source=TextSource.pdf_text,
                        text_sha256=_sha256(raw_text),
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        has_tables=_looks_like_table(raw_text),
                        processing_status=PageProcessingStatus.pending,
                    )
                    db.add(page_record)
                    db.flush()

                    for span in _extract_text_spans(page, raw_text):
                        db.add(
                            TextSpan(
                                document_id=document.id,
                                page_number=page_number,
                                char_start=span["char_start"],
                                char_end=span["char_end"],
                                bbox_x1=span["bbox_x1"],
                                bbox_y1=span["bbox_y1"],
                                bbox_x2=span["bbox_x2"],
                                bbox_y2=span["bbox_y2"],
                                span_text=span["span_text"],
                                span_sha256=span["span_sha256"],
                            )
                        )

                    db.commit()
                except Exception as exc:
                    db.rollback()
                    _persist_failed_page(db, document.id, page_number, f"pdf_page_parse_failed: {exc}")

        document.scanned_page_count = scanned_count
        db.add(document)
        db.commit()
    finally:
        db.close()
