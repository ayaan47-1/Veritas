from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .enums import DocumentType, ExtractionStage, ExtractionStatus


class PromptVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "prompt_versions"

    prompt_name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[DocumentType | None] = mapped_column(Enum(DocumentType))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("prompt_name", "version", name="uq_prompt_version"),
    )

    runs = relationship("ExtractionRun", back_populates="prompt_version")


class ExtractionRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "extraction_runs"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_versions.id"), nullable=False)
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stage: Mapped[ExtractionStage] = mapped_column(Enum(ExtractionStage), nullable=False)
    status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus), nullable=False, server_default=ExtractionStatus.running.value
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    raw_llm_output: Mapped[dict | None] = mapped_column(JSONB)

    document = relationship("Document", back_populates="extraction_runs", primaryjoin="Document.id==ExtractionRun.document_id")
    prompt_version = relationship(
        "PromptVersion", back_populates="runs", primaryjoin="PromptVersion.id==ExtractionRun.prompt_version_id"
    )

    __table_args__ = (
        Index("ix_extraction_runs_document_stage", "document_id", "stage"),
    )
