from __future__ import annotations

import hashlib
from pathlib import Path
import uuid

import fitz
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models import Chunk, Document, DocumentPage, DocumentType, PageProcessingStatus, ParseStatus, TextSource, TextSpan
from ._helpers import update_parse_status


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_HEURISTIC_RULES: list[tuple[DocumentType, list[str]]] = [
    # Most specific first
    (DocumentType.rfi, ["request for information", "rfi #", "rfi no"]),
    (DocumentType.change_order, ["change order", "change no.", "change no #", "contract modification"]),
    (DocumentType.invoice, ["invoice #", "invoice no", "bill to", "remit to", "payment due"]),
    (DocumentType.inspection_report, ["inspection report", "site inspection", "deficiency", "field observation"]),
    (DocumentType.lease, ["tenant", "landlord", "lessee", "lessor", "tenancy", "rental agreement"]),
    (DocumentType.contract, ["agreement", "whereas", "in witness whereof", "hereinafter"]),
]


def _detect_doc_type_heuristic(pages_text: list[str]) -> DocumentType | None:
    """Return a heuristic doc type from the first pages of raw text, or None if uncertain."""
    blob = " ".join(pages_text).lower()
    for doc_type, keywords in _HEURISTIC_RULES:
        if any(kw in blob for kw in keywords):
            return doc_type
    return None


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


def parse_document(document_id: str) -> dict[str, object]:
    update_parse_status(document_id, ParseStatus.parsing)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}

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
                detected = _detect_doc_type_heuristic([raw_text])
                if detected is not None:
                    document.doc_type = detected
                db.add(document)
                db.commit()
                return {
                    "document_id": str(document.id),
                    "status": "ok",
                    "file_type": "text",
                    "total_pages": 1,
                    "scanned_page_count": 0,
                    "failed_page_count": 0,
                    "text_span_count": 0,
                }
            except Exception as exc:
                db.rollback()
                _persist_failed_page(db, document.id, 1, f"txt_parse_failed: {exc}")
                document.total_pages = 1
                document.scanned_page_count = 0
                db.add(document)
                db.commit()
                return {
                    "document_id": str(document.id),
                    "status": "partial",
                    "file_type": "text",
                    "total_pages": 1,
                    "scanned_page_count": 0,
                    "failed_page_count": 1,
                    "text_span_count": 0,
                    "error": str(exc)[:200],
                }

        scanned_count = 0
        failed_page_count = 0
        try:
            pdf = fitz.open(document.file_path)
        except Exception as exc:
            document.parse_status = ParseStatus.failed
            document.notes = f"parse_open_failed: {exc}"
            db.add(document)
            db.commit()
            return {
                "document_id": str(document.id),
                "status": "failed",
                "file_type": "pdf",
                "error": str(exc)[:200],
            }

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
                    failed_page_count += 1
                    _persist_failed_page(db, document.id, page_number, f"pdf_page_parse_failed: {exc}")

        # If native parsing fails at page level, force OCR stage to try those pages.
        document.scanned_page_count = max(scanned_count, failed_page_count)

        # Heuristic doc type detection from raw text — gives an early guess before
        # the LLM classification stage runs. classify_document may override this.
        pages = (
            db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )[:3]
        page_texts = [str(p.raw_text or "").strip() for p in pages if (p.raw_text or "").strip()]
        if page_texts:
            detected = _detect_doc_type_heuristic(page_texts)
            if detected is not None:
                document.doc_type = detected

        db.add(document)
        db.commit()
        text_span_count = db.query(TextSpan).filter(TextSpan.document_id == document.id).count()
        return {
            "document_id": str(document.id),
            "status": "ok" if failed_page_count == 0 else "partial",
            "file_type": "pdf",
            "total_pages": int(document.total_pages or 0),
            "scanned_page_count": int(document.scanned_page_count or 0),
            "failed_page_count": failed_page_count,
            "text_span_count": text_span_count,
        }
    finally:
        db.close()
