from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import NotificationEvent, NotificationStatus, UserNotification

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _serialize_notification(notification: UserNotification, event: NotificationEvent | None) -> dict:
    return {
        "id": str(notification.id),
        "user_id": str(notification.user_id),
        "event_id": str(notification.event_id),
        "channel": notification.channel.value,
        "status": notification.status.value,
        "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "event": (
            {
                "event_type": event.event_type.value,
                "payload": event.payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            if event
            else None
        ),
    }


@router.get("")
def list_notifications(
    user_id: UUID = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(UserNotification)
        .filter(UserNotification.user_id == user_id)
        .order_by(UserNotification.id.desc())
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]

    event_ids = [row.event_id for row in items]
    events = db.query(NotificationEvent).filter(NotificationEvent.id.in_(event_ids)).all() if event_ids else []
    event_map = {row.id: row for row in events}

    return {
        "items": [_serialize_notification(row, event_map.get(row.event_id)) for row in items],
        "next_cursor": str(cursor + limit) if has_more else None,
    }


@router.put("/{notification_id}/read")
def mark_notification_read(notification_id: UUID, user_id: UUID = Query(...), db: Session = Depends(get_db)):
    notification = (
        db.query(UserNotification)
        .filter(UserNotification.id == notification_id, UserNotification.user_id == user_id)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.status = NotificationStatus.read
    notification.read_at = datetime.now(tz=timezone.utc)
    db.add(notification)
    db.commit()

    event = db.query(NotificationEvent).filter(NotificationEvent.id == notification.event_id).first()
    return _serialize_notification(notification, event)
