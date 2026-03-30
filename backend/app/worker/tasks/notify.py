from __future__ import annotations

import uuid

from ...config import settings
from ...database import SessionLocal
from ...models import (
    Document,
    DocumentPage,
    ExtractionRun,
    ExtractionStatus,
    NotificationChannel,
    NotificationEvent,
    NotificationEventType,
    NotificationStatus,
    PageProcessingStatus,
    ParseStatus,
    Risk,
    Severity,
    UserAssetAssignment,
    UserNotification,
)


def persist_final_status(document_id: str) -> dict[str, object]:
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            db.add(document)
            db.commit()
            return {
                "document_id": str(document.id),
                "status": "ok",
                "parse_status": document.parse_status.value,
                "failed_page_count": 0,
                "failed_extraction_run_count": 0,
            }
        failed_pages = (
            db.query(DocumentPage)
            .filter(
                DocumentPage.document_id == document.id,
                DocumentPage.processing_status == PageProcessingStatus.failed,
            )
            .count()
        )
        failed_extraction_runs = (
            db.query(ExtractionRun)
            .filter(
                ExtractionRun.document_id == document.id,
                ExtractionRun.status == ExtractionStatus.failed,
            )
            .count()
        )

        if failed_pages > 0 or failed_extraction_runs > 0:
            document.parse_status = ParseStatus.partially_processed
        else:
            document.parse_status = ParseStatus.complete
        db.add(document)
        db.commit()
        return {
            "document_id": str(document.id),
            "status": "ok",
            "parse_status": document.parse_status.value,
            "failed_page_count": failed_pages,
            "failed_extraction_run_count": failed_extraction_runs,
        }
    finally:
        db.close()


def emit_notifications(document_id: str) -> dict[str, object]:
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}

        recipients = {document.uploaded_by}
        assignments = (
            db.query(UserAssetAssignment)
            .filter(UserAssetAssignment.asset_id == document.asset_id)
            .all()
        )
        recipients.update(assignment.user_id for assignment in assignments)

        channels = [NotificationChannel.in_app]
        if _email_notifications_enabled():
            channels.append(NotificationChannel.email)

        processing_event = NotificationEvent(
            id=uuid.uuid4(),
            event_type=NotificationEventType.processing_complete,
            payload={
                "document_id": str(document.id),
                "asset_id": str(document.asset_id),
                "parse_status": document.parse_status.value,
                "source_name": document.source_name,
            },
        )
        db.add(processing_event)
        _create_user_notifications(db, processing_event.id, recipients, channels)

        risks = db.query(Risk).filter(Risk.document_id == document.id).all()
        elevated_risks = [risk for risk in risks if risk.severity in {Severity.high, Severity.critical}]
        event_count = 1
        if elevated_risks:
            risk_event = NotificationEvent(
                id=uuid.uuid4(),
                event_type=NotificationEventType.risk_detected,
                payload={
                    "document_id": str(document.id),
                    "asset_id": str(document.asset_id),
                    "high_or_critical_count": len(elevated_risks),
                },
            )
            db.add(risk_event)
            _create_user_notifications(db, risk_event.id, recipients, channels)
            event_count += 1

        db.commit()
        return {
            "document_id": str(document.id),
            "status": "ok",
            "recipient_count": len(recipients),
            "channel_count": len(channels),
            "event_count": event_count,
            "high_or_critical_risk_count": len(elevated_risks),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _create_user_notifications(
    db,
    event_id: uuid.UUID,
    recipients: set[uuid.UUID],
    channels: list[NotificationChannel],
) -> None:
    for user_id in recipients:
        for channel in channels:
            db.add(
                UserNotification(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    event_id=event_id,
                    channel=channel,
                    status=NotificationStatus.pending,
                )
            )


def _email_notifications_enabled() -> bool:
    notifications_cfg = settings.raw.get("notifications")
    if not isinstance(notifications_cfg, dict):
        return False
    return bool(notifications_cfg.get("email_enabled", False))
