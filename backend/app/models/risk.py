from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .enums import ReviewDecision, ReviewStatus, RiskType, Severity, TextSource


class Risk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "risks"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    risk_type: Mapped[RiskType] = mapped_column(Enum(RiskType), nullable=False)
    risk_text: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(Enum(Severity), nullable=False)
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus), nullable=False, server_default=ReviewStatus.needs_review.value
    )
    system_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_confidence: Mapped[int | None] = mapped_column(Integer)
    llm_severity: Mapped[Severity | None] = mapped_column(Enum(Severity))
    llm_quality_confidence: Mapped[int | None] = mapped_column(Integer)
    has_external_reference: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    contradiction_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    document = relationship("Document", back_populates="risks", primaryjoin="Document.id==Risk.document_id")
    evidence = relationship("RiskEvidence", back_populates="risk")
    reviews = relationship("RiskReview", back_populates="risk")

    __table_args__ = (
        CheckConstraint("system_confidence >= 0 AND system_confidence <= 100", name="ck_risk_sys_conf"),
        CheckConstraint(
            "reviewer_confidence IS NULL OR (reviewer_confidence >= 0 AND reviewer_confidence <= 100)",
            name="ck_risk_rev_conf",
        ),
        CheckConstraint(
            "llm_quality_confidence IS NULL OR (llm_quality_confidence >= 0 AND llm_quality_confidence <= 100)",
            name="ck_risk_llm_conf",
        ),
        Index("ix_risks_document_status", "document_id", "status"),
    )


class RiskEvidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "risk_evidence"

    risk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("risks.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_sha256: Mapped[str] = mapped_column(String, nullable=False)
    raw_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_x1: Mapped[float | None] = mapped_column(Float)
    bbox_y1: Mapped[float | None] = mapped_column(Float)
    bbox_x2: Mapped[float | None] = mapped_column(Float)
    bbox_y2: Mapped[float | None] = mapped_column(Float)
    source: Mapped[TextSource] = mapped_column(Enum(TextSource), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    risk = relationship("Risk", back_populates="evidence", primaryjoin="Risk.id==RiskEvidence.risk_id")

    __table_args__ = (
        UniqueConstraint(
            "quote_sha256",
            "document_id",
            "page_number",
            "normalized_char_start",
            "normalized_char_end",
            name="uq_risk_evidence_quote",
        ),
    )


class RiskReview(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "risk_reviews"

    risk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("risks.id"), nullable=False)
    decision: Mapped[ReviewDecision] = mapped_column(Enum(ReviewDecision), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    field_edits: Mapped[dict | None] = mapped_column(JSONB)
    reviewer_confidence: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    risk = relationship("Risk", back_populates="reviews", primaryjoin="Risk.id==RiskReview.risk_id")

    __table_args__ = (
        CheckConstraint(
            "reviewer_confidence IS NULL OR (reviewer_confidence >= 0 AND reviewer_confidence <= 100)",
            name="ck_risk_review_conf",
        ),
    )
