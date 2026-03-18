from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_admin, require_asset_scope, require_authenticated
from ..database import get_db
from ..models import Asset, AuditAction, AuditLog, Document, DocumentType, Obligation, ParseStatus, Risk, UserAssetAssignment

router = APIRouter(prefix="/assets", tags=["assets"])


class AssetCreateIn(BaseModel):
    name: str
    description: str | None = None
    created_by: UUID


def _serialize_asset(asset: Asset) -> dict:
    return {
        "id": str(asset.id),
        "name": asset.name,
        "description": asset.description,
        "created_by": str(asset.created_by),
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _serialize_document(document: Document) -> dict:
    return {
        "id": str(document.id),
        "asset_id": str(document.asset_id),
        "source_name": document.source_name,
        "doc_type": document.doc_type.value,
        "parse_status": document.parse_status.value,
        "uploaded_by": str(document.uploaded_by),
        "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
        "total_pages": document.total_pages,
        "scanned_page_count": document.scanned_page_count,
    }


@router.get("", dependencies=[Depends(require_authenticated)])
def list_assets(user_id: UUID | None = Query(default=None), db: Session = Depends(get_db)):
    query = db.query(Asset)
    if user_id is not None:
        query = query.join(UserAssetAssignment, UserAssetAssignment.asset_id == Asset.id).filter(
            UserAssetAssignment.user_id == user_id
        )
    assets = query.order_by(Asset.name.asc()).all()
    return {"items": [_serialize_asset(asset) for asset in assets], "next_cursor": None}


@router.post("", dependencies=[Depends(require_admin)])
def create_asset(payload: AssetCreateIn, db: Session = Depends(get_db)):
    asset = Asset(
        id=uuid.uuid4(),
        name=payload.name,
        description=payload.description,
        created_by=payload.created_by,
    )
    db.add(asset)
    audit = AuditLog(
        id=uuid.uuid4(),
        table_name="assets",
        record_id=asset.id,
        action=AuditAction.create,
        old_values=None,
        new_values={"name": payload.name},
        performed_by=payload.created_by,
        performed_at=datetime.now(tz=timezone.utc),
    )
    db.add(audit)
    db.commit()
    return _serialize_asset(asset)


@router.get("/{asset_id}", dependencies=[Depends(require_asset_scope("asset_id"))])
def get_asset(asset_id: UUID, db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    documents = db.query(Document).filter(Document.asset_id == asset_id).all()
    document_ids = [row.id for row in documents]
    obligations_count = db.query(Obligation).filter(Obligation.document_id.in_(document_ids)).count() if document_ids else 0
    risks_count = db.query(Risk).filter(Risk.document_id.in_(document_ids)).count() if document_ids else 0

    payload = _serialize_asset(asset)
    payload["document_count"] = len(documents)
    payload["obligation_count"] = obligations_count
    payload["risk_count"] = risks_count
    return payload


@router.get("/{asset_id}/documents", dependencies=[Depends(require_asset_scope("asset_id"))])
def list_asset_documents(
    asset_id: UUID,
    doc_type: DocumentType | None = Query(default=None),
    parse_status: ParseStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Document).filter(Document.asset_id == asset_id)
    if doc_type is not None:
        query = query.filter(Document.doc_type == doc_type)
    if parse_status is not None:
        query = query.filter(Document.parse_status == parse_status)

    rows = query.order_by(Document.uploaded_at.desc()).offset(cursor).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = [_serialize_document(row) for row in rows[:limit]]
    next_cursor = str(cursor + limit) if has_more else None
    return {"items": items, "next_cursor": next_cursor}
