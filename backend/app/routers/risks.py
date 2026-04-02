from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_asset_scope, require_reviewer_or_admin, require_risk_access
from ..database import get_db
from ..models import (
    AuditAction,
    AuditLog,
    Document,
    ReviewDecision,
    ReviewStatus,
    Risk,
    RiskEvidence,
    RiskReview,
    RiskType,
    Severity,
)

router = APIRouter(prefix="/risks", tags=["risks"])


class RiskReviewIn(BaseModel):
    decision: ReviewDecision
    reviewer_id: UUID
    field_edits: dict | None = None
    reviewer_confidence: int | None = None
    reason: str | None = None


def _serialize_evidence(evidence: RiskEvidence) -> dict:
    return {
        "id": str(evidence.id),
        "document_id": str(evidence.document_id),
        "page_number": evidence.page_number,
        "quote": evidence.quote,
        "raw_char_start": evidence.raw_char_start,
        "raw_char_end": evidence.raw_char_end,
        "normalized_char_start": evidence.normalized_char_start,
        "normalized_char_end": evidence.normalized_char_end,
        "source": evidence.source.value,
    }


def _serialize_risk(risk: Risk, *, evidence: list[RiskEvidence] | None = None) -> dict:
    payload = {
        "id": str(risk.id),
        "document_id": str(risk.document_id),
        "risk_type": risk.risk_type.value,
        "risk_text": risk.risk_text,
        "severity": risk.severity.value,
        "status": risk.status.value,
        "system_confidence": risk.system_confidence,
        "reviewer_confidence": risk.reviewer_confidence,
        "llm_severity": risk.llm_severity.value if risk.llm_severity else None,
        "llm_quality_confidence": risk.llm_quality_confidence,
        "has_external_reference": risk.has_external_reference,
        "contradiction_flag": risk.contradiction_flag,
        "created_at": risk.created_at.isoformat() if risk.created_at else None,
        "updated_at": risk.updated_at.isoformat() if risk.updated_at else None,
    }
    if evidence is not None:
        payload["evidence"] = [_serialize_evidence(row) for row in evidence]
    return payload


@router.get("", dependencies=[Depends(require_asset_scope("asset_id", required_for_non_admin=True))])
def list_risks(
    status: ReviewStatus | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    risk_type: RiskType | None = Query(default=None),
    document_id: UUID | None = Query(default=None),
    asset_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Risk)
    if status is not None:
        query = query.filter(Risk.status == status)
    if severity is not None:
        query = query.filter(Risk.severity == severity)
    if risk_type is not None:
        query = query.filter(Risk.risk_type == risk_type)
    if document_id is not None:
        query = query.filter(Risk.document_id == document_id)
    if asset_id is not None:
        query = query.join(Document, Risk.document_id == Document.id).filter(Document.asset_id == asset_id)

    total = query.count()
    rows = query.order_by(Risk.created_at.desc()).offset(cursor).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = [_serialize_risk(row) for row in rows[:limit]]
    next_cursor = str(cursor + limit) if has_more else None
    return {"items": items, "next_cursor": next_cursor, "total": total}


@router.get("/{risk_id}", dependencies=[Depends(require_risk_access("risk_id"))])
def get_risk(risk_id: UUID, db: Session = Depends(get_db)):
    risk = db.query(Risk).filter(Risk.id == risk_id).first()
    if not risk:
        raise HTTPException(status_code=404, detail="Risk not found")
    evidence = db.query(RiskEvidence).filter(RiskEvidence.risk_id == risk_id).all()
    return _serialize_risk(risk, evidence=evidence)


@router.post(
    "/{risk_id}/review",
    dependencies=[
        Depends(require_reviewer_or_admin),
        Depends(require_risk_access("risk_id")),
    ],
)
def review_risk(risk_id: UUID, payload: RiskReviewIn, db: Session = Depends(get_db)):
    risk = db.query(Risk).filter(Risk.id == risk_id).first()
    if not risk:
        raise HTTPException(status_code=404, detail="Risk not found")

    review = RiskReview(
        id=uuid.uuid4(),
        risk_id=risk.id,
        decision=payload.decision,
        reviewer_id=payload.reviewer_id,
        field_edits=payload.field_edits,
        reviewer_confidence=payload.reviewer_confidence,
        reason=payload.reason,
    )
    db.add(review)

    if payload.decision in {ReviewDecision.approve, ReviewDecision.edit_approve}:
        risk.status = ReviewStatus.confirmed
    else:
        risk.status = ReviewStatus.rejected
    if payload.reviewer_confidence is not None:
        risk.reviewer_confidence = payload.reviewer_confidence

    if payload.decision == ReviewDecision.edit_approve and payload.field_edits:
        editable_fields = {"risk_text", "risk_type", "severity"}
        for key, value in payload.field_edits.items():
            if key not in editable_fields:
                continue
            setattr(risk, key, value)

    audit = AuditLog(
        id=uuid.uuid4(),
        table_name="risks",
        record_id=risk.id,
        action=AuditAction.update,
        old_values=None,
        new_values={
            "status": risk.status.value,
            "review_decision": payload.decision.value,
            "reviewer_id": str(payload.reviewer_id),
        },
        performed_by=payload.reviewer_id,
        performed_at=datetime.now(tz=timezone.utc),
    )
    db.add(audit)
    db.add(risk)
    db.commit()

    return {
        "risk": _serialize_risk(risk),
        "review_id": str(review.id),
    }
