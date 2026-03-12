from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from ..models.enums import DocumentType, ParseStatus


class DocumentOut(BaseModel):
    id: UUID
    asset_id: UUID
    source_name: str
    file_path: str
    processed_file_path: Optional[str] = None
    sha256: str
    mime_type: str
    uploaded_by: UUID
    uploaded_at: datetime
    doc_type: DocumentType
    doc_type_confidence: Optional[float] = None
    doc_date: Optional[date] = None
    parse_status: ParseStatus
    total_pages: Optional[int] = None
    scanned_page_count: int
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


class DocumentStatus(BaseModel):
    document_id: UUID
    parse_status: ParseStatus
    total_pages: Optional[int] = None
    pages_processed: int
    pages_failed: int

