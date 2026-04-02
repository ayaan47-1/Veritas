from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .enums import ReportStatus, ResultStatus

if TYPE_CHECKING:
    from .ifc_model import IfcModel


class ComplianceReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "compliance_reports"

    ifc_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ifc_models.id", ondelete="CASCADE"),
        nullable=False,
    )
    spec_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus),
        nullable=False,
        server_default=ReportStatus.pending.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    total: Mapped[int | None] = mapped_column(Integer)
    passed: Mapped[int | None] = mapped_column(Integer)
    failed: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[int | None] = mapped_column(Integer)
    not_applicable: Mapped[int | None] = mapped_column(Integer)

    ifc_model: Mapped["IfcModel"] = relationship(
        "IfcModel", back_populates="compliance_reports"
    )
    results: Mapped[list["ComplianceResult"]] = relationship(
        "ComplianceResult",
        back_populates="report",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_compliance_reports_ifc_model_id", "ifc_model_id"),
        Index("ix_compliance_reports_spec_document_id", "spec_document_id"),
        Index("ix_compliance_reports_status", "status"),
    )


class ComplianceResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "compliance_results"

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compliance_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    section: Mapped[str] = mapped_column(String, nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    element_express_id: Mapped[int | None] = mapped_column(Integer)
    element_type: Mapped[str | None] = mapped_column(String)
    element_name: Mapped[str | None] = mapped_column(String)
    status: Mapped[ResultStatus] = mapped_column(
        Enum(ResultStatus), nullable=False
    )
    actual_value: Mapped[str | None] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    report: Mapped["ComplianceReport"] = relationship(
        "ComplianceReport", back_populates="results"
    )

    __table_args__ = (
        Index("ix_compliance_results_report_status", "report_id", "status"),
    )
