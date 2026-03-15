from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_admin
from ..config import settings
from ..database import get_db
from ..models import AuditAction, AuditLog, ConfigOverride

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdateIn(BaseModel):
    value: dict
    updated_by: UUID


def _set_nested(target: dict, dotted_key: str, value: dict) -> None:
    parts = [part for part in dotted_key.split(".") if part]
    if not parts:
        return
    cur = target
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


@router.get("", dependencies=[Depends(require_admin)])
def get_effective_config(db: Session = Depends(get_db)):
    overrides = db.query(ConfigOverride).all()
    by_key = {row.key: row.value for row in overrides}
    effective = copy.deepcopy(settings.raw)
    for key, value in by_key.items():
        _set_nested(effective, key, value)
    return {
        "base": settings.raw,
        "overrides": by_key,
        "effective": effective,
    }


@router.put("/{key}", dependencies=[Depends(require_admin)])
def upsert_config_override(key: str, payload: ConfigUpdateIn, db: Session = Depends(get_db)):
    override = db.query(ConfigOverride).filter(ConfigOverride.key == key).first()
    if override is None:
        override = ConfigOverride(
            id=uuid.uuid4(),
            key=key,
            value=payload.value,
            updated_by=payload.updated_by,
            updated_at=datetime.now(tz=timezone.utc),
        )
    else:
        override.value = payload.value
        override.updated_by = payload.updated_by
        override.updated_at = datetime.now(tz=timezone.utc)
    db.add(override)
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            table_name="config_overrides",
            record_id=override.id,
            action=AuditAction.update,
            old_values=None,
            new_values={"key": key},
            performed_by=payload.updated_by,
            performed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()
    return {"key": override.key, "value": override.value, "updated_by": str(override.updated_by)}


@router.delete("/{key}", dependencies=[Depends(require_admin)])
def delete_config_override(key: str, db: Session = Depends(get_db)):
    override = db.query(ConfigOverride).filter(ConfigOverride.key == key).first()
    if override is None:
        raise HTTPException(status_code=404, detail="Config override not found")

    deleted_id = override.id
    db.delete(override)
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            table_name="config_overrides",
            record_id=deleted_id,
            action=AuditAction.delete,
            old_values={"key": key},
            new_values=None,
            performed_by=None,
            performed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()
    return {"ok": True}
