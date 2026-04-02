from __future__ import annotations

import uuid

from sqlalchemy import Enum, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .enums import IfcParseStatus


class IfcModel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ifc_models"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    parse_status: Mapped[IfcParseStatus] = mapped_column(
        Enum(IfcParseStatus),
        nullable=False,
        server_default=IfcParseStatus.uploaded.value,
    )
    element_count: Mapped[int | None] = mapped_column(Integer)
    element_types: Mapped[dict | None] = mapped_column(JSONB)

    compliance_reports: Mapped[list] = relationship(
        "ComplianceReport", back_populates="ifc_model"
    )
