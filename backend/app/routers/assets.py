from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, AuditAction, AuditLog, Document, Obligation, Risk, UserAssetAssignment

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


@router.get("")
def list_assets(user_id: UUID | None = Query(default=None), db: Session = Depends(get_db)):
    query = db.query(Asset)
    if user_id is not None:
        query = query.join(UserAssetAssignment, UserAssetAssignment.asset_id == Asset.id).filter(
            UserAssetAssignment.user_id == user_id
        )
    assets = query.order_by(Asset.name.asc()).all()
    return {"items": [_serialize_asset(asset) for asset in assets], "next_cursor": None}


@router.post("")
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


@router.get("/{asset_id}")
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
