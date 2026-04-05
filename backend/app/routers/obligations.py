from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user, require_asset_scope, require_obligation_access, require_reviewer_or_admin
from ..database import get_db
from ..models import (
    Asset,
    AuditAction,
    AuditLog,
    Document,
    Obligation,
    ObligationEvidence,
    ObligationReview,
    ReviewDecision,
    ReviewStatus,
    Severity,
    User,
    UserRole,
)

router = APIRouter(prefix="/obligations", tags=["obligations"])


class ObligationReviewIn(BaseModel):
    decision: ReviewDecision
    reviewer_id: UUID
    field_edits: dict | None = None
    reviewer_confidence: int | None = None
    reason: str | None = None


def _serialize_evidence(evidence: ObligationEvidence) -> dict:
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


def _serialize_obligation(
    obligation: Obligation,
    *,
    evidence: list[ObligationEvidence] | None = None,
    document_domain: str | None = None,
) -> dict:
    if document_domain is None:
        document_domain = getattr(getattr(obligation, "document", None), "domain", None)
    payload = {
        "id": str(obligation.id),
        "document_id": str(obligation.document_id),
        "domain": document_domain,
        "document_domain": document_domain,
        "obligation_type": obligation.obligation_type.value,
        "obligation_text": obligation.obligation_text,
        "modality": obligation.modality.value,
        "responsible_entity_id": str(obligation.responsible_entity_id) if obligation.responsible_entity_id else None,
        "due_kind": obligation.due_kind.value,
        "due_date": obligation.due_date.isoformat() if obligation.due_date else None,
        "due_rule": obligation.due_rule,
        "trigger_date": obligation.trigger_date.isoformat() if obligation.trigger_date else None,
        "severity": obligation.severity.value,
        "status": obligation.status.value,
        "system_confidence": obligation.system_confidence,
        "reviewer_confidence": obligation.reviewer_confidence,
        "llm_severity": obligation.llm_severity.value if obligation.llm_severity else None,
        "llm_quality_confidence": obligation.llm_quality_confidence,
        "has_external_reference": obligation.has_external_reference,
        "contradiction_flag": obligation.contradiction_flag,
        "created_at": obligation.created_at.isoformat() if obligation.created_at else None,
        "updated_at": obligation.updated_at.isoformat() if obligation.updated_at else None,
    }
    if evidence is not None:
        payload["evidence"] = [_serialize_evidence(row) for row in evidence]
    return payload


@router.get("", dependencies=[Depends(require_asset_scope("asset_id", required_for_non_admin=True))])
def list_obligations(
    status: ReviewStatus | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    document_id: UUID | None = Query(default=None),
    asset_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Obligation)
    if status is not None:
        query = query.filter(Obligation.status == status)
    if severity is not None:
        query = query.filter(Obligation.severity == severity)
    if document_id is not None:
        query = query.filter(Obligation.document_id == document_id)
    if asset_id is not None:
        query = query.join(Document, Obligation.document_id == Document.id).filter(Document.asset_id == asset_id)
    elif current_user.role == UserRole.admin:
        query = query.join(Document, Obligation.document_id == Document.id).join(
            Asset, Document.asset_id == Asset.id
        ).filter(Asset.created_by == current_user.id)

    total = query.count()
    rows = query.order_by(Obligation.created_at.desc()).offset(cursor).limit(limit + 1).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    document_ids = [row.document_id for row in page_rows]
    domains_by_document = {
        row.id: row.domain for row in db.query(Document).filter(Document.id.in_(document_ids)).all()
    } if document_ids else {}
    items = [
        _serialize_obligation(row, document_domain=domains_by_document.get(row.document_id))
        for row in page_rows
    ]
    next_cursor = str(cursor + limit) if has_more else None
    return {"items": items, "next_cursor": next_cursor, "total": total}


@router.get("/{obligation_id}", dependencies=[Depends(require_obligation_access("obligation_id"))])
def get_obligation(obligation_id: UUID, db: Session = Depends(get_db)):
    obligation = db.query(Obligation).filter(Obligation.id == obligation_id).first()
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    evidence = db.query(ObligationEvidence).filter(ObligationEvidence.obligation_id == obligation_id).all()
    return _serialize_obligation(obligation, evidence=evidence)


@router.post(
    "/{obligation_id}/review",
    dependencies=[
        Depends(require_reviewer_or_admin),
        Depends(require_obligation_access("obligation_id")),
    ],
)
def review_obligation(obligation_id: UUID, payload: ObligationReviewIn, db: Session = Depends(get_db)):
    obligation = db.query(Obligation).filter(Obligation.id == obligation_id).first()
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")

    review = ObligationReview(
        id=uuid.uuid4(),
        obligation_id=obligation.id,
        decision=payload.decision,
        reviewer_id=payload.reviewer_id,
        field_edits=payload.field_edits,
        reviewer_confidence=payload.reviewer_confidence,
        reason=payload.reason,
    )
    db.add(review)

    if payload.decision in {ReviewDecision.approve, ReviewDecision.edit_approve}:
        obligation.status = ReviewStatus.confirmed
    else:
        obligation.status = ReviewStatus.rejected
    if payload.reviewer_confidence is not None:
        obligation.reviewer_confidence = payload.reviewer_confidence

    if payload.decision == ReviewDecision.edit_approve and payload.field_edits:
        editable_fields = {
            "obligation_text",
            "responsible_entity_id",
            "due_rule",
            "due_date",
            "trigger_date",
            "severity",
            "modality",
        }
        for key, value in payload.field_edits.items():
            if key not in editable_fields:
                continue
            setattr(obligation, key, value)

    audit = AuditLog(
        id=uuid.uuid4(),
        table_name="obligations",
        record_id=obligation.id,
        action=AuditAction.update,
        old_values=None,
        new_values={
            "status": obligation.status.value,
            "review_decision": payload.decision.value,
            "reviewer_id": str(payload.reviewer_id),
        },
        performed_by=payload.reviewer_id,
        performed_at=datetime.now(tz=timezone.utc),
    )
    db.add(audit)
    db.add(obligation)
    db.commit()

    return {
        "obligation": _serialize_obligation(obligation),
        "review_id": str(review.id),
    }
