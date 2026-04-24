from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user, require_admin, require_authenticated
from ..database import get_db
from ..models import AuditAction, AuditLog, User, UserAssetAssignment, UserRole
from ..services.unsubscribe_token import InvalidTokenError, verify_unsubscribe_token

router = APIRouter(prefix="/users", tags=["users"])


class UserRoleUpdateIn(BaseModel):
    role: UserRole
    updated_by: UUID | None = None


class PreferencesIn(BaseModel):
    digest_enabled: bool | None = None
    digest_timezone: str | None = None


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


@router.get("/me", dependencies=[Depends(require_authenticated)])
def get_me(current_user: User = Depends(get_current_user)):
    return _serialize_user(current_user)


def _serialize_preferences(user: User) -> dict:
    return {
        "digest_enabled": user.digest_enabled,
        "digest_timezone": user.digest_timezone,
    }


@router.get("/me/preferences", dependencies=[Depends(require_authenticated)])
def get_my_preferences(current_user: User = Depends(get_current_user)):
    return _serialize_preferences(current_user)


@router.put("/me/preferences", dependencies=[Depends(require_authenticated)])
def update_my_preferences(
    payload: PreferencesIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.digest_enabled is None and payload.digest_timezone is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    if payload.digest_timezone is not None:
        try:
            ZoneInfo(payload.digest_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid timezone: {payload.digest_timezone}",
            ) from exc
        current_user.digest_timezone = payload.digest_timezone

    if payload.digest_enabled is not None:
        current_user.digest_enabled = payload.digest_enabled

    db.add(current_user)
    db.commit()
    return _serialize_preferences(current_user)


@router.post("/unsubscribe/{token}")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    """One-click unsubscribe endpoint — intentionally no auth.

    Accepts a signed HMAC token minted for a specific user. Idempotent.
    """
    secret = os.getenv("DIGEST_UNSUBSCRIBE_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Unsubscribe not available",
        )
    try:
        user_id = verify_unsubscribe_token(token, secret)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=404, detail="Invalid token") from exc

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user.digest_enabled:
        user.digest_enabled = False
        db.add(user)
        db.commit()
    return {"ok": True, "email": user.email}


@router.get("", dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {"items": [_serialize_user(user) for user in users], "next_cursor": None}


@router.put("/{user_id}/role", dependencies=[Depends(require_admin)])
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


@router.get("/{user_id}/assets", dependencies=[Depends(require_admin)])
def get_user_assets(user_id: UUID, db: Session = Depends(get_db)):
    assignments = db.query(UserAssetAssignment).filter(UserAssetAssignment.user_id == user_id).all()
    return [{"id": str(a.id), "user_id": str(a.user_id), "asset_id": str(a.asset_id)} for a in assignments]


@router.post("/{user_id}/assets", dependencies=[Depends(require_admin)])
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


@router.delete("/{user_id}/assets/{asset_id}", dependencies=[Depends(require_admin)])
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
