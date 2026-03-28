from __future__ import annotations

import uuid
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .enums import DueKind, Modality, ObligationType, ReviewDecision, ReviewStatus, Severity, TextSource


class Obligation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "obligations"

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    obligation_type: Mapped[ObligationType] = mapped_column(Enum(ObligationType), nullable=False)
    obligation_text: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[Modality] = mapped_column(Enum(Modality), nullable=False)
    responsible_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"))
    due_kind: Mapped[DueKind] = mapped_column(Enum(DueKind), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    due_rule: Mapped[str | None] = mapped_column(Text)
    trigger_date: Mapped[date | None] = mapped_column(Date)
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

    document = relationship("Document", back_populates="obligations", primaryjoin="Document.id==Obligation.document_id")
    evidence = relationship("ObligationEvidence", back_populates="obligation")
    reviews = relationship("ObligationReview", back_populates="obligation")

    __table_args__ = (
        CheckConstraint("system_confidence >= 0 AND system_confidence <= 100", name="ck_obligation_sys_conf"),
        CheckConstraint(
            "reviewer_confidence IS NULL OR (reviewer_confidence >= 0 AND reviewer_confidence <= 100)",
            name="ck_obligation_rev_conf",
        ),
        CheckConstraint(
            "llm_quality_confidence IS NULL OR (llm_quality_confidence >= 0 AND llm_quality_confidence <= 100)",
            name="ck_obligation_llm_conf",
        ),
        Index("ix_obligations_document_status", "document_id", "status"),
        Index("ix_obligations_status_severity", "status", "severity"),
        Index("ix_obligations_due_date", "due_date", postgresql_where=text("due_date IS NOT NULL")),
    )


class ObligationEvidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "obligation_evidence"

    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False)
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

    obligation = relationship("Obligation", back_populates="evidence", primaryjoin="Obligation.id==ObligationEvidence.obligation_id")

    __table_args__ = (
        UniqueConstraint(
            "quote_sha256",
            "document_id",
            "page_number",
            "normalized_char_start",
            "normalized_char_end",
            name="uq_obligation_evidence_quote",
        ),
    )


class ObligationReview(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "obligation_reviews"

    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("obligations.id"), nullable=False)
    decision: Mapped[ReviewDecision] = mapped_column(Enum(ReviewDecision), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    field_edits: Mapped[dict | None] = mapped_column(JSONB)
    reviewer_confidence: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    obligation = relationship("Obligation", back_populates="reviews", primaryjoin="Obligation.id==ObligationReview.obligation_id")

    __table_args__ = (
        CheckConstraint(
            "reviewer_confidence IS NULL OR (reviewer_confidence >= 0 AND reviewer_confidence <= 100)",
            name="ck_obligation_review_conf",
        ),
    )


class ObligationContradiction(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "obligation_contradictions"

    obligation_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("obligations.id"), nullable=False)
    obligation_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("obligations.id"), nullable=False)
    risk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("risks.id"), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("obligation_a_id < obligation_b_id", name="ck_obligation_pair_order"),
        UniqueConstraint("obligation_a_id", "obligation_b_id", name="uq_obligation_pair"),
    )
