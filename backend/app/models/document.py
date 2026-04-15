from __future__ import annotations

import uuid
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .enums import (
    DocumentType,
    PageProcessingStatus,
    ParseStatus,
    SplitReason,
    TextSource,
)


class Document(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "documents"

    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    processed_file_path: Mapped[str | None] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    doc_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType), nullable=False, server_default=DocumentType.unknown.value
    )
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    doc_date: Mapped[date | None] = mapped_column(Date)
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus), nullable=False, server_default=ParseStatus.uploaded.value
    )
    total_pages: Mapped[int | None] = mapped_column(Integer)
    scanned_page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("sha256", "asset_id", name="uq_document_sha256_asset"),
    )

    asset = relationship("Asset", back_populates="documents", primaryjoin="Asset.id==Document.asset_id")
    pages = relationship("DocumentPage", back_populates="document")
    text_spans = relationship("TextSpan", back_populates="document")
    chunks = relationship("Chunk", back_populates="document")
    extraction_runs = relationship("ExtractionRun", back_populates="document")
    obligations = relationship("Obligation", back_populates="document")
    risks = relationship("Risk", back_populates="document")


class DocumentPage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    text_source: Mapped[TextSource] = mapped_column(Enum(TextSource), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String, nullable=False)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    has_tables: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    processing_status: Mapped[PageProcessingStatus] = mapped_column(
        Enum(PageProcessingStatus), nullable=False, server_default=PageProcessingStatus.pending.value
    )
    processing_error: Mapped[str | None] = mapped_column(Text)

    document = relationship("Document", back_populates="pages", primaryjoin="Document.id==DocumentPage.document_id")

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_page"),
    )


class TextSpan(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "text_spans"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_x1: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y1: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_x2: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y2: Mapped[float] = mapped_column(Float, nullable=False)
    span_text: Mapped[str] = mapped_column(Text, nullable=False)
    span_sha256: Mapped[str] = mapped_column(String, nullable=False)

    document = relationship("Document", back_populates="text_spans", primaryjoin="Document.id==TextSpan.document_id")

    __table_args__ = (
        Index("ix_text_spans_document_page", "document_id", "page_number"),
    )


class Chunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # TODO: replace with pgvector when enabled.
    embedding: Mapped[dict | None] = mapped_column(JSONB)
    chunk_sha256: Mapped[str] = mapped_column(String, nullable=False)
    split_reason: Mapped[SplitReason] = mapped_column(Enum(SplitReason), nullable=False)
    section_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document", back_populates="chunks", primaryjoin="Document.id==Chunk.document_id")

    __table_args__ = (
        Index("ix_chunks_document_page", "document_id", "page_number"),
    )
