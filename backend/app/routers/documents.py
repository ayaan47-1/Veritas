from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import inngest

from ..auth.deps import require_authenticated
from ..database import get_db
from ..models import (
    Document,
    DocumentPage,
    EntityMention,
    ExtractionRun,
    Obligation,
    ObligationContradiction,
    ObligationEvidence,
    ObligationReview,
    PageProcessingStatus,
    ParseStatus,
    Risk,
    RiskEvidence,
    RiskReview,
    TextSpan,
)
from ..schemas.documents import DocumentOut, DocumentPageOut, DocumentStatus
from ..worker.inngest_client import inngest_client

router = APIRouter(prefix="/documents", tags=["documents"])


def _get_document_or_404(document_id: UUID, db: Session) -> Document:
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: UUID, db: Session = Depends(get_db)):
    document = _get_document_or_404(document_id, db)
    return document


@router.post("/{document_id}/process", dependencies=[Depends(require_authenticated)])
async def process_document(document_id: UUID, db: Session = Depends(get_db)):
    document = _get_document_or_404(document_id, db)
    if document.parse_status != ParseStatus.uploaded:
        raise HTTPException(
            status_code=409,
            detail=f"Document is already in {document.parse_status.value} state",
        )

    await inngest_client.send(
        inngest.Event(
            name="veritas/document.uploaded",
            data={"document_id": str(document.id)},
        )
    )
    return {"ok": True}


@router.get("/{document_id}/status", response_model=DocumentStatus)
def get_document_status(document_id: UUID, db: Session = Depends(get_db)):
    document = _get_document_or_404(document_id, db)

    pages_processed = (
        db.query(func.count(DocumentPage.id))
        .filter(
            DocumentPage.document_id == document_id,
            DocumentPage.processing_status == PageProcessingStatus.processed,
        )
        .scalar()
        or 0
    )
    pages_failed = (
        db.query(func.count(DocumentPage.id))
        .filter(
            DocumentPage.document_id == document_id,
            DocumentPage.processing_status == PageProcessingStatus.failed,
        )
        .scalar()
        or 0
    )

    return DocumentStatus(
        document_id=document.id,
        parse_status=document.parse_status,
        total_pages=document.total_pages,
        pages_processed=pages_processed,
        pages_failed=pages_failed,
    )


@router.get("/{document_id}/pages/{page_number}", response_model=DocumentPageOut)
def get_document_page(document_id: UUID, page_number: int, db: Session = Depends(get_db)):
    _get_document_or_404(document_id, db)
    page = (
        db.query(DocumentPage)
        .filter(
            DocumentPage.document_id == document_id,
            DocumentPage.page_number == page_number,
        )
        .first()
    )
    if not page:
        raise HTTPException(status_code=404, detail="Document page not found")

    spans = (
        db.query(TextSpan)
        .filter(
            TextSpan.document_id == document_id,
            TextSpan.page_number == page_number,
        )
        .order_by(TextSpan.char_start.asc())
        .all()
    )

    serialized_spans = [
        {
            "id": str(span.id),
            "char_start": span.char_start,
            "char_end": span.char_end,
            "bbox_x1": span.bbox_x1,
            "bbox_y1": span.bbox_y1,
            "bbox_x2": span.bbox_x2,
            "bbox_y2": span.bbox_y2,
            "span_text": span.span_text,
        }
        for span in spans
    ]

    return {
        "document_id": str(page.document_id),
        "page_number": page.page_number,
        "raw_text": page.raw_text,
        "normalized_text": page.normalized_text,
        "text_source": page.text_source.value,
        "processing_status": page.processing_status.value,
        "processing_error": page.processing_error,
        "text_spans": serialized_spans,
    }


@router.delete("/{document_id}", dependencies=[Depends(require_authenticated)])
def delete_document(document_id: UUID, db: Session = Depends(get_db)):
    document = _get_document_or_404(document_id, db)

    obligation_ids = [r.id for r in db.query(Obligation).filter(Obligation.document_id == document_id).all()]
    risk_ids = [r.id for r in db.query(Risk).filter(Risk.document_id == document_id).all()]

    contradiction_filters = []
    if obligation_ids:
        contradiction_filters.extend([
            ObligationContradiction.obligation_a_id.in_(obligation_ids),
            ObligationContradiction.obligation_b_id.in_(obligation_ids),
        ])
    if risk_ids:
        contradiction_filters.append(ObligationContradiction.risk_id.in_(risk_ids))
    if contradiction_filters:
        db.query(ObligationContradiction).filter(or_(*contradiction_filters)).delete(synchronize_session=False)

    if obligation_ids:
        db.query(ObligationEvidence).filter(ObligationEvidence.obligation_id.in_(obligation_ids)).delete(synchronize_session=False)
        db.query(ObligationReview).filter(ObligationReview.obligation_id.in_(obligation_ids)).delete(synchronize_session=False)
        db.query(Obligation).filter(Obligation.id.in_(obligation_ids)).delete(synchronize_session=False)

    if risk_ids:
        db.query(RiskEvidence).filter(RiskEvidence.risk_id.in_(risk_ids)).delete(synchronize_session=False)
        db.query(RiskReview).filter(RiskReview.risk_id.in_(risk_ids)).delete(synchronize_session=False)
        db.query(Risk).filter(Risk.id.in_(risk_ids)).delete(synchronize_session=False)

    db.query(EntityMention).filter(EntityMention.document_id == document_id).delete(synchronize_session=False)
    db.query(ExtractionRun).filter(ExtractionRun.document_id == document_id).delete(synchronize_session=False)

    file_path = document.file_path
    db.query(Document).filter(Document.id == document_id).delete(synchronize_session=False)
    db.commit()

    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass

    return {"ok": True}


@router.get("/{document_id}/pdf")
def get_document_pdf(
    document_id: UUID,
    processed: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    document = _get_document_or_404(document_id, db)

    file_path = document.file_path
    if processed and document.processed_file_path:
        file_path = document.processed_file_path

    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="Document file not found")

    return FileResponse(file_path, media_type=document.mime_type, filename=document.source_name)
