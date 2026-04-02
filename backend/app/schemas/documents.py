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
    domain: Optional[str] = None
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


class TextSpanOut(BaseModel):
    id: UUID
    char_start: int
    char_end: int
    bbox_x1: float
    bbox_y1: float
    bbox_x2: float
    bbox_y2: float
    span_text: str

    model_config = {"from_attributes": True}


class DocumentPageOut(BaseModel):
    document_id: UUID
    page_number: int
    raw_text: str
    normalized_text: str
    text_source: str
    processing_status: str
    processing_error: Optional[str] = None
    text_spans: list[TextSpanOut]
