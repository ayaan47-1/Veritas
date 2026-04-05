from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user, require_asset_scope
from ..database import get_db
from ..models import Asset, Document, Obligation, Risk, User, UserRole

router = APIRouter(prefix="/summary", tags=["summaries"])


def _weekly_metrics(asset_id: UUID | None, db: Session, current_user: User | None = None) -> dict:
    document_query = db.query(Document)
    if asset_id is not None:
        document_query = document_query.filter(Document.asset_id == asset_id)
    elif current_user is not None and current_user.role == UserRole.admin:
        admin_asset_ids = [
            row.id for row in db.query(Asset).filter(Asset.created_by == current_user.id).all()
        ]
        document_query = document_query.filter(Document.asset_id.in_(admin_asset_ids))
    documents = document_query.all()
    document_ids = [row.id for row in documents]

    if document_ids:
        obligations = db.query(Obligation).filter(Obligation.document_id.in_(document_ids)).all()
        risks = db.query(Risk).filter(Risk.document_id.in_(document_ids)).all()
    else:
        obligations = []
        risks = []

    obligations_by_status: dict[str, int] = {}
    for row in obligations:
        key = row.status.value
        obligations_by_status[key] = obligations_by_status.get(key, 0) + 1

    risks_by_severity: dict[str, int] = {}
    for row in risks:
        key = row.severity.value
        risks_by_severity[key] = risks_by_severity.get(key, 0) + 1

    return {
        "asset_id": str(asset_id) if asset_id else None,
        "documents_total": len(documents),
        "obligations_total": len(obligations),
        "risks_total": len(risks),
        "obligations_by_status": obligations_by_status,
        "risks_by_severity": risks_by_severity,
    }


@router.get("/weekly", dependencies=[Depends(require_asset_scope("asset_id", required_for_non_admin=True))])
def get_weekly_summary(
    asset_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _weekly_metrics(asset_id=asset_id, db=db, current_user=current_user)


@router.get("/weekly/narrative", dependencies=[Depends(require_asset_scope("asset_id", required_for_non_admin=True))])
def get_weekly_narrative(
    asset_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    metrics = _weekly_metrics(asset_id=asset_id, db=db, current_user=current_user)
    narrative = (
        f"Processed {metrics['documents_total']} documents with "
        f"{metrics['obligations_total']} obligations and {metrics['risks_total']} risks."
    )
    return {
        "asset_id": metrics["asset_id"],
        "narrative": narrative,
        "metrics": metrics,
    }
