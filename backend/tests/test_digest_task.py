from __future__ import annotations

import hashlib
import types
import uuid
from datetime import date, timedelta

from backend.app.models import (
    Asset,
    Document,
    Obligation,
    ParseStatus,
    ReviewStatus,
    Severity,
    User,
    UserAssetAssignment,
)
from backend.app.models.enums import (
    DueKind,
    Modality,
    OIDCProvider,
    ObligationType,
    UserRole,
)
from backend.app.worker.tasks import digest as digest_task


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._predicates: list = []

    def filter(self, *expressions):
        self._predicates.extend(expressions)
        return self

    def all(self):
        return [row for row in self._rows() if self._matches_all(row)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def _rows(self):
        if self._model is User:
            return list(self._session.users)
        if self._model is UserAssetAssignment:
            return list(self._session.assignments)
        if self._model is Document:
            return list(self._session.documents)
        if self._model is Asset:
            return list(self._session.assets)
        if self._model is Obligation:
            return list(self._session.obligations)
        return []

    def _matches_all(self, row):
        return all(self._matches(row, p) for p in self._predicates)

    def _matches(self, row, expr):
        # Support `Column == value` and `Column.in_([...])`.
        operator = getattr(expr, "operator", None)
        left = getattr(expr, "left", None)
        key = getattr(left, "key", None)
        if key is None:
            return True
        value = getattr(row, key, None)

        # `.in_([...])` produces operator with name "in_op" in SQLA.
        op_name = getattr(operator, "__name__", "")
        if op_name in {"in_op"}:
            clauses = getattr(getattr(expr, "right", None), "clauses", None)
            if clauses is not None:
                candidates = [getattr(c, "value", c) for c in clauses]
                return value in candidates
            # Fallback: right may be a plain list-expression.
            return True

        right = getattr(expr, "right", None)
        right_value = getattr(right, "value", right)
        return value == right_value


class FakeSession:
    def __init__(
        self,
        *,
        users: list[User] | None = None,
        assignments: list[UserAssetAssignment] | None = None,
        documents: list[Document] | None = None,
        assets: list[Asset] | None = None,
        obligations: list[Obligation] | None = None,
    ):
        self.users = users or []
        self.assignments = assignments or []
        self.documents = documents or []
        self.assets = assets or []
        self.obligations = obligations or []

    def query(self, model):
        return FakeQuery(self, model)

    def close(self):
        return None


def _make_user(*, email: str = "u@example.com") -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        name="Test User",
        oidc_provider=OIDCProvider.clerk,
        oidc_subject="sub",
        role=UserRole.reviewer,
        is_active=True,
        digest_enabled=True,
        digest_timezone="America/Chicago",
    )


def _make_asset(name: str) -> Asset:
    return Asset(id=uuid.uuid4(), name=name)


def _make_document(asset_id: uuid.UUID, uploader_id: uuid.UUID) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uploader_id,
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


def _make_obligation(
    *,
    document_id: uuid.UUID,
    due_date: date | None,
    severity: Severity,
    status: ReviewStatus = ReviewStatus.confirmed,
    text: str = "Pay rent on time.",
) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        document_id=document_id,
        obligation_type=ObligationType.payment,
        obligation_text=text,
        modality=Modality.shall,
        due_kind=DueKind.absolute,
        due_date=due_date,
        severity=severity,
        status=status,
        system_confidence=80,
        has_external_reference=False,
        contradiction_flag=False,
    )


def _scaffold(*, user, assets, documents, obligations):
    assignments = [
        UserAssetAssignment(id=uuid.uuid4(), user_id=user.id, asset_id=a.id)
        for a in assets
    ]
    return FakeSession(
        users=[user],
        assignments=assignments,
        documents=documents,
        assets=assets,
        obligations=obligations,
    )


def _settings_stub():
    return types.SimpleNamespace(
        raw={
            "digest": {
                "from_address": "digest@veritaslayer.net",
                "public_base_url": "https://veritaslayer.net",
                "enabled": True,
            }
        }
    )


def test_empty_digest_returns_none(monkeypatch):
    user = _make_user()
    asset = _make_asset("Asset A")
    doc = _make_document(asset.id, user.id)
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=[])
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    assert digest_task.compose_user_digest(db, user.id, today=date(2026, 4, 20)) is None


def test_user_with_no_assets_returns_none(monkeypatch):
    user = _make_user()
    db = FakeSession(users=[user])
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)
    assert digest_task.compose_user_digest(db, user.id, today=date(2026, 4, 20)) is None


def test_critical_only_subject(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Lease 1")
    doc = _make_document(asset.id, user.id)
    obs = [
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=3), severity=Severity.critical
        ),
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=5), severity=Severity.high
        ),
    ]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    payload = digest_task.compose_user_digest(db, user.id, today=today)
    assert payload is not None
    assert payload["subject"] == "2 critical obligations due this week"
    assert payload["critical_count"] == 2
    assert payload["item_count"] == 2
    assert payload["recipient"] == user.email


def test_mixed_sections_render_in_correct_order(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Lease Beta")
    doc = _make_document(asset.id, user.id)
    obs = [
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=2),
            severity=Severity.critical, text="CRIT-early"
        ),
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=10),
            severity=Severity.medium, text="MID-window"
        ),
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=28),
            severity=Severity.low, text="LATE-window"
        ),
    ]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    payload = digest_task.compose_user_digest(db, user.id, today=today)
    assert payload is not None
    assert payload["subject"] == "1 critical obligations due this week"
    html = payload["html"]
    # Critical section appears before mid, before late.
    crit_idx = html.index("CRIT-early")
    mid_idx = html.index("MID-window")
    late_idx = html.index("LATE-window")
    assert crit_idx < mid_idx < late_idx
    assert "Critical this week" in html
    assert "Due in the next 14 days" in html
    assert "Coming up in 30 days" in html


def test_rejected_status_excluded(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Lease")
    doc = _make_document(asset.id, user.id)
    obs = [
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=5),
            severity=Severity.high, status=ReviewStatus.rejected
        ),
    ]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    assert digest_task.compose_user_digest(db, user.id, today=today) is None


def test_null_due_date_excluded(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Lease")
    doc = _make_document(asset.id, user.id)
    obs = [_make_obligation(document_id=doc.id, due_date=None, severity=Severity.high)]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    assert digest_task.compose_user_digest(db, user.id, today=today) is None


def test_due_date_beyond_window_excluded(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Lease")
    doc = _make_document(asset.id, user.id)
    obs = [
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=45),
            severity=Severity.high
        ),
        _make_obligation(
            document_id=doc.id, due_date=today - timedelta(days=1),
            severity=Severity.high
        ),
    ]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    assert digest_task.compose_user_digest(db, user.id, today=today) is None


def test_assets_not_assigned_excluded(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    mine = _make_asset("Mine")
    theirs = _make_asset("Theirs")
    mine_doc = _make_document(mine.id, user.id)
    theirs_doc = _make_document(theirs.id, user.id)
    obs = [
        _make_obligation(
            document_id=mine_doc.id, due_date=today + timedelta(days=5),
            severity=Severity.high, text="MINE"
        ),
        _make_obligation(
            document_id=theirs_doc.id, due_date=today + timedelta(days=5),
            severity=Severity.critical, text="NOT-MINE"
        ),
    ]
    # Only `mine` is assigned. theirs_doc is excluded at the asset_ids step.
    assignments = [
        UserAssetAssignment(id=uuid.uuid4(), user_id=user.id, asset_id=mine.id)
    ]
    db = FakeSession(
        users=[user],
        assignments=assignments,
        documents=[mine_doc, theirs_doc],
        assets=[mine, theirs],
        obligations=obs,
    )
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    payload = digest_task.compose_user_digest(db, user.id, today=today)
    assert payload is not None
    assert payload["item_count"] == 1
    assert "MINE" in payload["html"]
    assert "NOT-MINE" not in payload["html"]


def test_within_bucket_sorted_by_due_date_then_severity(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Asset")
    doc = _make_document(asset.id, user.id)
    # All three land in "due_next_14_days" bucket (severity medium / low / medium).
    obs = [
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=13),
            severity=Severity.medium, text="THIRD-late"
        ),
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=10),
            severity=Severity.low, text="SECOND-low"
        ),
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=10),
            severity=Severity.medium, text="FIRST-medium"
        ),
    ]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    payload = digest_task.compose_user_digest(db, user.id, today=today)
    html = payload["html"]  # type: ignore[index]
    # FIRST-medium before SECOND-low (same date, higher severity), then THIRD-late.
    assert html.index("FIRST-medium") < html.index("SECOND-low") < html.index("THIRD-late")


def test_standard_subject_without_critical(monkeypatch):
    today = date(2026, 4, 20)
    user = _make_user()
    asset = _make_asset("Lease")
    doc = _make_document(asset.id, user.id)
    obs = [
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=10),
            severity=Severity.medium
        ),
        _make_obligation(
            document_id=doc.id, due_date=today + timedelta(days=20),
            severity=Severity.low
        ),
    ]
    db = _scaffold(user=user, assets=[asset], documents=[doc], obligations=obs)
    monkeypatch.setattr(digest_task, "settings", _settings_stub(), raising=False)

    payload = digest_task.compose_user_digest(db, user.id, today=today)
    assert payload is not None
    assert payload["subject"].startswith("Your VeritasLayer weekly digest")
    assert "2 obligations approaching" in payload["subject"]


def test_truncate_text_over_limit():
    long = "a" * 200
    out = digest_task._truncate_text(long, limit=120)
    assert out.endswith("…")
    assert len(out) <= 121


def test_truncate_text_under_limit():
    short = "short text"
    assert digest_task._truncate_text(short, limit=120) == "short text"
