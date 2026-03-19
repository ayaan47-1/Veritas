from __future__ import annotations

import hashlib
import importlib
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException



from backend.app.models import (
    Document,
    DueKind,
    Modality,
    NotificationChannel,
    NotificationEvent,
    NotificationEventType,
    NotificationStatus,
    Obligation,
    ObligationEvidence,
    ObligationType,
    ParseStatus,
    ReviewDecision,
    ReviewStatus,
    Severity,
    TextSource,
    UserNotification,
)
notifications_router = importlib.import_module("backend.app.routers.notifications")
obligations_router = importlib.import_module("backend.app.routers.obligations")


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []
        self._offset = 0
        self._limit = None

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def join(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def offset(self, offset: int):
        self._offset = offset
        return self

    def limit(self, limit: int):
        self._limit = limit
        return self

    def all(self):
        rows = [row for row in self._rows_for_model() if self._matches_all(row)]
        rows = self._sorted(rows)
        rows = rows[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.all())

    def _rows_for_model(self):
        if self._model is obligations_router.Document:
            return list(self._session.documents)
        if self._model is obligations_router.Obligation:
            return list(self._session.obligations)
        if self._model is obligations_router.ObligationEvidence:
            return list(self._session.obligation_evidence)
        if self._model is obligations_router.ObligationReview:
            return list(self._session.obligation_reviews)
        if self._model is obligations_router.AuditLog:
            return list(self._session.audit_logs)
        if self._model is notifications_router.UserNotification:
            return list(self._session.user_notifications)
        if self._model is notifications_router.NotificationEvent:
            return list(self._session.notification_events)
        return []

    def _sorted(self, rows):
        if not rows:
            return rows
        if self._model is obligations_router.Obligation:
            return sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        if self._model is notifications_router.UserNotification:
            return sorted(rows, key=lambda row: str(row.id), reverse=True)
        return rows

    def _matches_all(self, row):
        return all(self._matches(row, condition) for condition in self._conditions)

    def _matches(self, row, condition):
        left = getattr(condition, "left", None)
        right = getattr(condition, "right", None)
        if left is None:
            return True

        key = getattr(left, "key", None)
        if key is None:
            return True

        row_value = self._row_value(row, key)
        operator = getattr(getattr(condition, "operator", None), "__name__", "")
        raw_value = getattr(right, "value", right)

        if operator == "in_op":
            return row_value in list(raw_value)
        if operator == "is_":
            return row_value is raw_value
        return row_value == raw_value

    def _row_value(self, row, key: str):
        if hasattr(row, key):
            return getattr(row, key)
        if key == "asset_id" and hasattr(row, "document_id"):
            document = self._session.document_by_id.get(row.document_id)
            return document.asset_id if document else None
        return None


class FakeSession:
    def __init__(
        self,
        *,
        documents: list[Document] | None = None,
        obligations: list[Obligation] | None = None,
        obligation_evidence: list[ObligationEvidence] | None = None,
        user_notifications: list[UserNotification] | None = None,
        notification_events: list[NotificationEvent] | None = None,
    ):
        self.documents = documents or []
        self.document_by_id = {row.id: row for row in self.documents}
        self.obligations = obligations or []
        self.obligation_evidence = obligation_evidence or []
        self.obligation_reviews = []
        self.audit_logs = []
        self.user_notifications = user_notifications or []
        self.notification_events = notification_events or []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, obligations_router.ObligationReview):
            self.obligation_reviews.append(obj)
            return
        if isinstance(obj, obligations_router.AuditLog):
            self.audit_logs.append(obj)
            return
        if isinstance(obj, obligations_router.Obligation):
            if obj not in self.obligations:
                self.obligations.append(obj)
            return
        if isinstance(obj, notifications_router.UserNotification):
            if obj not in self.user_notifications:
                self.user_notifications.append(obj)
            return

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _make_document(asset_id: uuid.UUID) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(str(asset_id).encode("utf-8")).hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


def _make_obligation(
    document_id: uuid.UUID,
    *,
    status: ReviewStatus = ReviewStatus.needs_review,
    severity: Severity = Severity.high,
    created_at: datetime | None = None,
) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        document_id=document_id,
        obligation_type=ObligationType.payment,
        obligation_text="Contractor shall pay in full.",
        modality=Modality.shall,
        responsible_entity_id=None,
        due_kind=DueKind.none,
        due_date=None,
        due_rule=None,
        trigger_date=None,
        severity=severity,
        status=status,
        system_confidence=75,
        reviewer_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
        created_at=created_at,
    )


def _make_obligation_evidence(document_id: uuid.UUID, obligation_id: uuid.UUID) -> ObligationEvidence:
    quote = "Contractor shall pay in full."
    return ObligationEvidence(
        id=uuid.uuid4(),
        obligation_id=obligation_id,
        document_id=document_id,
        page_number=1,
        quote=quote,
        quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        raw_char_start=0,
        raw_char_end=len(quote),
        normalized_char_start=0,
        normalized_char_end=len(quote),
        source=TextSource.pdf_text,
    )


def _make_notification_event(event_type: NotificationEventType) -> NotificationEvent:
    return NotificationEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        payload={"k": "v"},
    )


def _make_user_notification(user_id: uuid.UUID, event_id: uuid.UUID) -> UserNotification:
    return UserNotification(
        id=uuid.uuid4(),
        user_id=user_id,
        event_id=event_id,
        channel=NotificationChannel.in_app,
        status=NotificationStatus.pending,
    )


def test_list_obligations_filters_and_paginates():
    asset_a = uuid.uuid4()
    asset_b = uuid.uuid4()
    doc_a = _make_document(asset_a)
    doc_b = _make_document(asset_b)
    now = datetime.now(tz=UTC)

    obligations = [
        _make_obligation(doc_a.id, created_at=now - timedelta(minutes=1)),
        _make_obligation(doc_a.id, created_at=now - timedelta(minutes=2)),
        _make_obligation(doc_b.id, created_at=now - timedelta(minutes=3)),
        _make_obligation(doc_a.id, severity=Severity.low, created_at=now - timedelta(minutes=4)),
    ]
    db = FakeSession(documents=[doc_a, doc_b], obligations=obligations)

    page1 = obligations_router.list_obligations(
        status=ReviewStatus.needs_review,
        severity=Severity.high,
        document_id=None,
        asset_id=asset_a,
        limit=1,
        cursor=0,
        db=db,
    )
    page2 = obligations_router.list_obligations(
        status=ReviewStatus.needs_review,
        severity=Severity.high,
        document_id=None,
        asset_id=asset_a,
        limit=1,
        cursor=1,
        db=db,
    )

    assert len(page1["items"]) == 1
    assert page1["next_cursor"] == "1"
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None
    assert all(item["severity"] == Severity.high.value for item in page1["items"] + page2["items"])


def test_get_obligation_returns_404_when_missing():
    db = FakeSession(documents=[], obligations=[])
    with pytest.raises(HTTPException) as exc:
        obligations_router.get_obligation(uuid.uuid4(), db=db)
    assert exc.value.status_code == 404


def test_review_obligation_updates_status_and_writes_audit():
    doc = _make_document(uuid.uuid4())
    obligation = _make_obligation(doc.id)
    evidence = _make_obligation_evidence(doc.id, obligation.id)
    db = FakeSession(documents=[doc], obligations=[obligation], obligation_evidence=[evidence])
    reviewer_id = uuid.uuid4()

    response = obligations_router.review_obligation(
        obligation.id,
        obligations_router.ObligationReviewIn(
            decision=ReviewDecision.approve,
            reviewer_id=reviewer_id,
            reviewer_confidence=92,
            reason="Looks valid",
        ),
        db=db,
    )

    assert response["obligation"]["status"] == ReviewStatus.confirmed.value
    assert obligation.reviewer_confidence == 92
    assert len(db.obligation_reviews) == 1
    assert len(db.audit_logs) == 1
    assert db.audit_logs[0].table_name == "obligations"
    assert db.audit_logs[0].action == obligations_router.AuditAction.update


def test_notifications_list_and_mark_read_behavior():
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    event_a = _make_notification_event(NotificationEventType.processing_complete)
    event_b = _make_notification_event(NotificationEventType.risk_detected)
    user_notification_a = _make_user_notification(user_id, event_a.id)
    user_notification_b = _make_user_notification(user_id, event_b.id)
    other_notification = _make_user_notification(other_user_id, event_a.id)
    db = FakeSession(
        user_notifications=[user_notification_a, user_notification_b, other_notification],
        notification_events=[event_a, event_b],
    )

    listed = notifications_router.list_notifications(user_id=user_id, limit=1, cursor=0, db=db)
    assert len(listed["items"]) == 1
    assert listed["next_cursor"] == "1"
    assert listed["items"][0]["event"] is not None

    read = notifications_router.mark_notification_read(user_notification_a.id, user_id=user_id, db=db)
    assert read["status"] == NotificationStatus.read.value
    assert user_notification_a.read_at is not None

    with pytest.raises(HTTPException) as exc:
        notifications_router.mark_notification_read(other_notification.id, user_id=user_id, db=db)
    assert exc.value.status_code == 404
