from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from ..models.enums import ReportStatus, ResultStatus


class ComplianceReportIn(BaseModel):
    ifc_model_id: UUID
    spec_document_id: UUID


class ComplianceReportOut(BaseModel):
    id: UUID
    ifc_model_id: UUID
    spec_document_id: UUID
    created_by: UUID
    status: ReportStatus
    error_message: Optional[str] = None
    total: Optional[int] = None
    passed: Optional[int] = None
    failed: Optional[int] = None
    warnings: Optional[int] = None
    not_applicable: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ComplianceResultOut(BaseModel):
    id: UUID
    report_id: UUID
    rule_id: str
    section: str
    requirement: str
    element_express_id: Optional[int] = None
    element_type: Optional[str] = None
    element_name: Optional[str] = None
    status: ResultStatus
    actual_value: Optional[str] = None
    message: str

    model_config = {"from_attributes": True}


class ComplianceResultsPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ComplianceResultOut]
