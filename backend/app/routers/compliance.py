from __future__ import annotations

from uuid import UUID

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ComplianceReport, ComplianceResult, Document, IfcModel
from ..models.enums import ReportStatus
from ..schemas.compliance import (
    ComplianceReportIn,
    ComplianceReportOut,
    ComplianceResultOut,
    ComplianceResultsPage,
)
from ..worker.inngest_client import inngest_client

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _get_report_or_404(report_id: UUID, db: Session) -> ComplianceReport:
    report = db.query(ComplianceReport).filter(
        ComplianceReport.id == report_id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Compliance report not found")
    return report


@router.post(
    "/reports",
    response_model=ComplianceReportOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_compliance_report(
    body: ComplianceReportIn,
    created_by: UUID = Query(..., description="UUID of the requesting user"),
    db: Session = Depends(get_db),
) -> ComplianceReportOut:
    """Trigger a compliance check between an IFC model and a spec PDF.

    The check runs asynchronously via Inngest. Poll GET /compliance/reports/{id}
    until status is 'completed' or 'failed'.
    """
    ifc_model = db.query(IfcModel).filter(
        IfcModel.id == body.ifc_model_id
    ).first()
    if not ifc_model:
        raise HTTPException(status_code=404, detail="IFC model not found")

    spec_doc = db.query(Document).filter(
        Document.id == body.spec_document_id
    ).first()
    if not spec_doc:
        raise HTTPException(status_code=404, detail="Spec document not found")

    report = ComplianceReport(
        ifc_model_id=body.ifc_model_id,
        spec_document_id=body.spec_document_id,
        created_by=created_by,
        status=ReportStatus.pending,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    await inngest_client.send(
        inngest.Event(
            name="veritas/compliance.requested",
            data={"report_id": str(report.id)},
        )
    )

    return report


@router.get("/reports/{report_id}", response_model=ComplianceReportOut)
def get_compliance_report(
    report_id: UUID,
    db: Session = Depends(get_db),
) -> ComplianceReportOut:
    """Get a compliance report and its summary counts."""
    return _get_report_or_404(report_id, db)


@router.get(
    "/reports/{report_id}/results",
    response_model=ComplianceResultsPage,
)
def get_compliance_results(
    report_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ComplianceResultsPage:
    """List compliance results for a report, paginated."""
    _get_report_or_404(report_id, db)

    total = (
        db.query(ComplianceResult)
        .filter(ComplianceResult.report_id == report_id)
        .count()
    )
    items = (
        db.query(ComplianceResult)
        .filter(ComplianceResult.report_id == report_id)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return ComplianceResultsPage(
        total=total,
        offset=offset,
        limit=limit,
        items=items,
    )
