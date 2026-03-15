from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_authenticated, require_reviewer_or_admin
from ..database import get_db
from ..models import AuditAction, AuditLog, Entity, EntityMention

router = APIRouter(prefix="", tags=["entities"])


class MergeEntityIn(BaseModel):
    source_entity_id: UUID
    merged_by: UUID | None = None


class ResolveMentionIn(BaseModel):
    entity_id: UUID
    resolved_by: UUID


def _serialize_entity(entity: Entity) -> dict:
    return {
        "id": str(entity.id),
        "canonical_name": entity.canonical_name,
        "entity_type": entity.entity_type.value,
        "aliases": list(entity.aliases or []),
        "created_at": entity.created_at.isoformat() if entity.created_at else None,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
    }


def _serialize_mention(mention: EntityMention) -> dict:
    return {
        "id": str(mention.id),
        "entity_id": str(mention.entity_id) if mention.entity_id else None,
        "document_id": str(mention.document_id),
        "mentioned_name": mention.mentioned_name,
        "page_number": mention.page_number,
        "suggested_entity_id": str(mention.suggested_entity_id) if mention.suggested_entity_id else None,
        "resolved": mention.resolved,
        "resolved_by": str(mention.resolved_by) if mention.resolved_by else None,
        "created_at": mention.created_at.isoformat() if mention.created_at else None,
    }


@router.get("/entities", dependencies=[Depends(require_authenticated)])
def list_entities(
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    rows = db.query(Entity).order_by(Entity.canonical_name.asc()).offset(cursor).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = [_serialize_entity(row) for row in rows[:limit]]
    next_cursor = str(cursor + limit) if has_more else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/entities/suggestions", dependencies=[Depends(require_reviewer_or_admin)])
def list_entity_suggestions(
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(EntityMention)
        .filter(EntityMention.resolved.is_(False), EntityMention.suggested_entity_id.is_not(None))
        .order_by(EntityMention.created_at.desc())
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    items = [_serialize_mention(row) for row in rows[:limit]]
    next_cursor = str(cursor + limit) if has_more else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("/entities/{entity_id}/merge", dependencies=[Depends(require_reviewer_or_admin)])
def merge_entity(entity_id: UUID, payload: MergeEntityIn, db: Session = Depends(get_db)):
    target = db.query(Entity).filter(Entity.id == entity_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target entity not found")
    source = db.query(Entity).filter(Entity.id == payload.source_entity_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source entity not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Source and target entity must differ")

    mentions = db.query(EntityMention).filter(EntityMention.entity_id == source.id).all()
    for mention in mentions:
        mention.entity_id = target.id
        db.add(mention)
    suggested_mentions = db.query(EntityMention).filter(EntityMention.suggested_entity_id == source.id).all()
    for mention in suggested_mentions:
        mention.suggested_entity_id = target.id
        db.add(mention)

    aliases = set(target.aliases or [])
    aliases.add(target.canonical_name)
    aliases.add(source.canonical_name)
    aliases.update(source.aliases or [])
    target.aliases = sorted(aliases)

    audit = AuditLog(
        id=uuid.uuid4(),
        table_name="entities",
        record_id=target.id,
        action=AuditAction.update,
        old_values=None,
        new_values={"merged_source_entity_id": str(source.id)},
        performed_by=payload.merged_by,
        performed_at=datetime.now(tz=timezone.utc),
    )
    db.add(audit)
    db.add(target)
    db.delete(source)
    db.commit()
    return _serialize_entity(target)


@router.post("/entity-mentions/{mention_id}/resolve", dependencies=[Depends(require_reviewer_or_admin)])
def resolve_entity_mention(mention_id: UUID, payload: ResolveMentionIn, db: Session = Depends(get_db)):
    mention = db.query(EntityMention).filter(EntityMention.id == mention_id).first()
    if not mention:
        raise HTTPException(status_code=404, detail="Entity mention not found")
    entity = db.query(Entity).filter(Entity.id == payload.entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    mention.entity_id = payload.entity_id
    mention.resolved = True
    mention.resolved_by = payload.resolved_by
    db.add(mention)
    db.commit()
    return _serialize_mention(mention)
