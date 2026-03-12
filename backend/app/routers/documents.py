from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, DocumentPage, PageProcessingStatus
from ..schemas.documents import DocumentOut, DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: UUID, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}/status", response_model=DocumentStatus)
def get_document_status(document_id: UUID, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

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

