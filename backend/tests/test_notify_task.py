from __future__ import annotations

import hashlib
import sys
import types
import uuid



from backend.app.models import (
    Document,
    NotificationChannel,
    NotificationEventType,
    NotificationStatus,
    ParseStatus,
    Risk,
    RiskType,
    ReviewStatus,
    Severity,
    UserAssetAssignment,
)
from backend.app.worker.tasks import notify as notify_task


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def all(self):
        return [row for row in self._rows_for_model() if self._matches_all(row)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.all())

    def _rows_for_model(self):
        if self._model is notify_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is notify_task.UserAssetAssignment:
            return list(self._session.assignments)
        if self._model is notify_task.Risk:
            return list(self._session.risks)
        if self._model is notify_task.NotificationEvent:
            return list(self._session.events)
        if self._model is notify_task.UserNotification:
            return list(self._session.user_notifications)
        return []

    def _matches_all(self, row):
        return all(self._matches(row, condition) for condition in self._conditions)

    def _matches(self, row, condition):
        left = getattr(condition, "left", None)
        right = getattr(condition, "right", None)
        if left is None or right is None:
            return True

        key = getattr(left, "key", None)
        if key is None:
            return True

        value = getattr(right, "value", right)
        return getattr(row, key) == value


class FakeSession:
    def __init__(
        self,
        *,
        document: Document | None = None,
        assignments: list[UserAssetAssignment] | None = None,
        risks: list[Risk] | None = None,
    ):
        self.document = document
        self.assignments = assignments or []
        self.risks = risks or []
        self.events = []
        self.user_notifications = []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, notify_task.NotificationEvent):
            if obj not in self.events:
                self.events.append(obj)
            return
        if isinstance(obj, notify_task.UserNotification):
            if obj not in self.user_notifications:
                self.user_notifications.append(obj)
            return

    def commit(self):
        return None

    def rollback(self):
        return None

    def flush(self):
        return None

    def close(self):
        return None


def _make_document(uploaded_by: uuid.UUID) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uploaded_by,
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


def _make_assignment(user_id: uuid.UUID, asset_id: uuid.UUID) -> UserAssetAssignment:
    return UserAssetAssignment(id=uuid.uuid4(), user_id=user_id, asset_id=asset_id)


def _make_risk(document_id: uuid.UUID, severity: Severity) -> Risk:
    return Risk(
        id=uuid.uuid4(),
        document_id=document_id,
        risk_type=RiskType.scope_change_indicator,
        risk_text="Potential issue",
        severity=severity,
        status=ReviewStatus.needs_review,
        system_confidence=0,
        reviewer_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
    )


def test_emit_notifications_creates_processing_complete_with_recipient_fanout(monkeypatch):
    uploader_id = uuid.uuid4()
    assigned_1 = uuid.uuid4()
    assigned_2 = uuid.uuid4()
    document = _make_document(uploaded_by=uploader_id)
    assignments = [
        _make_assignment(uploader_id, document.asset_id),  # duplicated recipient, should de-dupe
        _make_assignment(assigned_1, document.asset_id),
        _make_assignment(assigned_2, document.asset_id),
    ]
    db = FakeSession(document=document, assignments=assignments)

    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(notify_task, "settings", types.SimpleNamespace(raw={}), raising=False)

    notify_task.emit_notifications(document.id)

    assert len(db.events) == 1
    assert db.events[0].event_type == NotificationEventType.processing_complete
    assert db.events[0].payload["document_id"] == str(document.id)

    assert len(db.user_notifications) == 3
    recipients = {notification.user_id for notification in db.user_notifications}
    assert recipients == {uploader_id, assigned_1, assigned_2}
    assert all(notification.channel == NotificationChannel.in_app for notification in db.user_notifications)
    assert all(notification.status == NotificationStatus.pending for notification in db.user_notifications)


def test_emit_notifications_adds_email_channel_when_enabled(monkeypatch):
    uploader_id = uuid.uuid4()
    assigned_1 = uuid.uuid4()
    document = _make_document(uploaded_by=uploader_id)
    assignments = [_make_assignment(assigned_1, document.asset_id)]
    db = FakeSession(document=document, assignments=assignments)

    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        notify_task, "settings", types.SimpleNamespace(raw={"notifications": {"email_enabled": True}}), raising=False
    )

    notify_task.emit_notifications(document.id)

    assert len(db.events) == 1
    assert db.events[0].event_type == NotificationEventType.processing_complete

    assert len(db.user_notifications) == 4
    by_channel = {}
    for notification in db.user_notifications:
        by_channel.setdefault(notification.channel, set()).add(notification.user_id)
    assert by_channel[NotificationChannel.in_app] == {uploader_id, assigned_1}
    assert by_channel[NotificationChannel.email] == {uploader_id, assigned_1}


def test_emit_notifications_creates_risk_detected_event_for_high_or_critical(monkeypatch):
    uploader_id = uuid.uuid4()
    document = _make_document(uploaded_by=uploader_id)
    risks = [
        _make_risk(document.id, Severity.medium),
        _make_risk(document.id, Severity.high),
        _make_risk(document.id, Severity.critical),
    ]
    db = FakeSession(document=document, assignments=[], risks=risks)

    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(notify_task, "settings", types.SimpleNamespace(raw={}), raising=False)

    notify_task.emit_notifications(document.id)

    assert len(db.events) == 2
    event_types = {event.event_type for event in db.events}
    assert event_types == {NotificationEventType.processing_complete, NotificationEventType.risk_detected}
    risk_event = next(event for event in db.events if event.event_type == NotificationEventType.risk_detected)
    assert risk_event.payload["document_id"] == str(document.id)
    assert risk_event.payload["high_or_critical_count"] == 2


def test_emit_notifications_noops_for_missing_document(monkeypatch):
    db = FakeSession(document=None, assignments=[], risks=[])
    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db)

    notify_task.emit_notifications(str(uuid.uuid4()))

    assert db.events == []
    assert db.user_notifications == []
