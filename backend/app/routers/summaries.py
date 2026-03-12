from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, Obligation, Risk

router = APIRouter(prefix="/summary", tags=["summaries"])


def _weekly_metrics(asset_id: UUID | None, db: Session) -> dict:
    document_query = db.query(Document)
    if asset_id is not None:
        document_query = document_query.filter(Document.asset_id == asset_id)
    documents = document_query.all()
    document_ids = [row.id for row in documents]

    obligations_query = db.query(Obligation)
    risks_query = db.query(Risk)
    if asset_id is not None:
        obligations_query = obligations_query.filter(Obligation.document_id.in_(document_ids))
        risks_query = risks_query.filter(Risk.document_id.in_(document_ids))
    obligations = obligations_query.all()
    risks = risks_query.all()

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


@router.get("/weekly")
def get_weekly_summary(asset_id: UUID | None = Query(default=None), db: Session = Depends(get_db)):
    return _weekly_metrics(asset_id=asset_id, db=db)


@router.get("/weekly/narrative")
def get_weekly_narrative(asset_id: UUID | None = Query(default=None), db: Session = Depends(get_db)):
    metrics = _weekly_metrics(asset_id=asset_id, db=db)
    narrative = (
        f"Processed {metrics['documents_total']} documents with "
        f"{metrics['obligations_total']} obligations and {metrics['risks_total']} risks."
    )
    return {
        "asset_id": metrics["asset_id"],
        "narrative": narrative,
        "metrics": metrics,
    }
