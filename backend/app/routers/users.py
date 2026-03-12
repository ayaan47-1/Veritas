from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditAction, AuditLog, User, UserAssetAssignment, UserRole

router = APIRouter(prefix="/users", tags=["users"])


class UserRoleUpdateIn(BaseModel):
    role: UserRole
    updated_by: UUID | None = None


class UserAssetAssignIn(BaseModel):
    asset_id: UUID
    assigned_by: UUID | None = None


def _serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "oidc_provider": user.oidc_provider.value,
        "role": user.role.value,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("/me")
def get_me(user_id: UUID = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _serialize_user(user)


@router.get("")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {"items": [_serialize_user(user) for user in users], "next_cursor": None}


@router.put("/{user_id}/role")
def update_user_role(user_id: UUID, payload: UserRoleUpdateIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = payload.role
    db.add(user)
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            table_name="users",
            record_id=user.id,
            action=AuditAction.update,
            old_values=None,
            new_values={"role": user.role.value},
            performed_by=payload.updated_by,
            performed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()
    return _serialize_user(user)


@router.post("/{user_id}/assets")
def assign_user_asset(user_id: UUID, payload: UserAssetAssignIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (
        db.query(UserAssetAssignment)
        .filter(UserAssetAssignment.user_id == user_id, UserAssetAssignment.asset_id == payload.asset_id)
        .first()
    )
    if existing:
        return {"id": str(existing.id), "user_id": str(existing.user_id), "asset_id": str(existing.asset_id)}

    assignment = UserAssetAssignment(
        id=uuid.uuid4(),
        user_id=user_id,
        asset_id=payload.asset_id,
    )
    db.add(assignment)
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            table_name="user_asset_assignments",
            record_id=assignment.id,
            action=AuditAction.create,
            old_values=None,
            new_values={"user_id": str(user_id), "asset_id": str(payload.asset_id)},
            performed_by=payload.assigned_by,
            performed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()
    return {"id": str(assignment.id), "user_id": str(assignment.user_id), "asset_id": str(assignment.asset_id)}


@router.delete("/{user_id}/assets/{asset_id}")
def remove_user_asset(user_id: UUID, asset_id: UUID, db: Session = Depends(get_db)):
    assignment = (
        db.query(UserAssetAssignment)
        .filter(UserAssetAssignment.user_id == user_id, UserAssetAssignment.asset_id == asset_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    db.delete(assignment)
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            table_name="user_asset_assignments",
            record_id=assignment.id,
            action=AuditAction.delete,
            old_values={"user_id": str(user_id), "asset_id": str(asset_id)},
            new_values=None,
            performed_by=None,
            performed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()
    return {"ok": True}
