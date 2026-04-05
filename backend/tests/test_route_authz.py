from __future__ import annotations

import hashlib
import importlib
import sys
import types
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient



from backend.app.auth import deps as auth_deps
from backend.app.database import get_db
from backend.app.main import create_app
from backend.app.models import (
    Asset,
    Document,
    DueKind,
    Modality,
    Obligation,
    ObligationType,
    OIDCProvider,
    ParseStatus,
    ReviewStatus,
    Risk,
    RiskType,
    Severity,
    User,
    UserAssetAssignment,
    UserRole,
)


obligations_router = importlib.import_module("backend.app.routers.obligations")
risks_router = importlib.import_module("backend.app.routers.risks")
assets_router = importlib.import_module("backend.app.routers.assets")
config_router = importlib.import_module("backend.app.routers.config")


class FakeQuery:
    def __init__(self, session: "FakeSession", *models):
        self._session = session
        self._model = models[0] if models else None
        self._models = models
        self._conditions = []
        self._offset = 0
        self._limit = None
        self._is_aggregate = len(models) > 1

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

    def group_by(self, *_args):
        return self

    def all(self):
        if self._is_aggregate:
            return []
        rows = [row for row in self._rows_for_model() if self._matches_all(row)]
        if self._model is obligations_router.Obligation:
            rows = sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
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
        if self._model is auth_deps.User:
            return list(self._session.users)
        if self._model is auth_deps.UserAssetAssignment:
            return list(self._session.assignments)
        if self._model is auth_deps.Asset:
            return list(self._session.assets)
        if self._model is auth_deps.Obligation:
            return list(self._session.obligations)
        if self._model is auth_deps.Document:
            return list(self._session.documents)
        if self._model is obligations_router.Obligation:
            return list(self._session.obligations)
        if self._model is obligations_router.Document:
            return list(self._session.documents)
        if self._model is obligations_router.Asset:
            return list(self._session.assets)
        if self._model is obligations_router.ObligationEvidence:
            return []
        if self._model is obligations_router.ObligationReview:
            return list(self._session.obligation_reviews)
        if self._model is obligations_router.AuditLog:
            return list(self._session.audit_logs)
        if self._model is risks_router.Risk:
            return list(self._session.risks)
        if self._model is risks_router.Document:
            return list(self._session.documents)
        if self._model is risks_router.Asset:
            return list(self._session.assets)
        if self._model is risks_router.RiskEvidence:
            return []
        if self._model is assets_router.Asset:
            return list(self._session.assets)
        if self._model is assets_router.Document:
            return list(self._session.documents)
        if self._model is assets_router.Obligation:
            return list(self._session.obligations)
        if self._model is assets_router.Risk:
            return list(self._session.risks)
        if self._model is assets_router.UserAssetAssignment:
            return list(self._session.assignments)
        if self._model is config_router.ConfigOverride:
            return list(self._session.config_overrides)
        if self._model is config_router.AuditLog:
            return list(self._session.audit_logs)
        return []

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
        if key == "created_by" and hasattr(row, "document_id"):
            document = self._session.document_by_id.get(row.document_id)
            if document:
                asset = self._session.asset_by_id.get(document.asset_id)
                return asset.created_by if asset else None
        return None


class FakeSession:
    def __init__(
        self,
        *,
        users: list[User] | None = None,
        assignments: list[UserAssetAssignment] | None = None,
        assets: list[Asset] | None = None,
        documents: list[Document] | None = None,
        obligations: list[Obligation] | None = None,
        risks: list[Risk] | None = None,
    ):
        self.users = users or []
        self.assignments = assignments or []
        self.assets = assets or []
        self.asset_by_id = {row.id: row for row in self.assets}
        self.documents = documents or []
        self.document_by_id = {row.id: row for row in self.documents}
        self.obligations = obligations or []
        self.risks = risks or []
        self.obligation_reviews = []
        self.audit_logs = []
        self.config_overrides = []

    def query(self, *models):
        return FakeQuery(self, *models)

    def add(self, obj):
        if isinstance(obj, obligations_router.ObligationReview):
            self.obligation_reviews.append(obj)
            return
        if isinstance(obj, obligations_router.AuditLog):
            self.audit_logs.append(obj)
            return
        if isinstance(obj, config_router.AuditLog):
            self.audit_logs.append(obj)
            return
        if isinstance(obj, obligations_router.Obligation):
            if obj not in self.obligations:
                self.obligations.append(obj)
            return

    def delete(self, obj):
        if obj in self.config_overrides:
            self.config_overrides.remove(obj)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _make_user(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{role.value}@example.com",
        name=role.value,
        oidc_provider=OIDCProvider.clerk,
        oidc_subject=f"{role.value}-subject",
        role=role,
        is_active=True,
    )


def _make_asset(created_by: uuid.UUID) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        name=f"Asset-{uuid.uuid4().hex[:6]}",
        description=None,
        created_by=created_by,
    )


def _make_risk(document_id: uuid.UUID) -> Risk:
    return Risk(
        id=uuid.uuid4(),
        document_id=document_id,
        risk_type=RiskType.financial,
        risk_text="Financial risk identified.",
        severity=Severity.medium,
        status=ReviewStatus.needs_review,
        system_confidence=70,
        reviewer_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
    )


def _make_document(asset_id: uuid.UUID, uploaded_by: uuid.UUID) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(str(asset_id).encode("utf-8")).hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uploaded_by,
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


def _make_obligation(document_id: uuid.UUID) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        document_id=document_id,
        obligation_type=ObligationType.payment,
        obligation_text="Contractor shall pay.",
        modality=Modality.shall,
        responsible_entity_id=None,
        due_kind=DueKind.none,
        due_date=None,
        due_rule=None,
        trigger_date=None,
        severity=Severity.high,
        status=ReviewStatus.needs_review,
        system_confidence=80,
        reviewer_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
    )


def _build_client(db: FakeSession) -> TestClient:
    app = create_app()

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def test_obligations_list_requires_authentication_header():
    db = FakeSession()
    client = _build_client(db)
    response = client.get(f"/obligations?asset_id={uuid.uuid4()}")
    assert response.status_code == 401


def test_obligation_access_and_review_role_guards():
    viewer = _make_user(UserRole.viewer)
    reviewer = _make_user(UserRole.reviewer)
    asset_id = uuid.uuid4()
    document = _make_document(asset_id=asset_id, uploaded_by=viewer.id)
    obligation = _make_obligation(document.id)
    assignment_viewer = UserAssetAssignment(id=uuid.uuid4(), user_id=viewer.id, asset_id=asset_id)
    assignment_reviewer = UserAssetAssignment(id=uuid.uuid4(), user_id=reviewer.id, asset_id=asset_id)
    db = FakeSession(
        users=[viewer, reviewer],
        assignments=[assignment_viewer, assignment_reviewer],
        documents=[document],
        obligations=[obligation],
    )
    client = _build_client(db)

    app = client.app
    app.dependency_overrides[auth_deps.get_current_user] = lambda: viewer
    response_no_scope = client.get("/obligations")
    assert response_no_scope.status_code == 403

    response_scoped = client.get(f"/obligations?asset_id={asset_id}")
    assert response_scoped.status_code == 200

    review_payload = {
        "decision": "approve",
        "reviewer_id": str(reviewer.id),
        "reviewer_confidence": 90,
    }
    response_viewer_review = client.post(f"/obligations/{obligation.id}/review", json=review_payload)
    assert response_viewer_review.status_code == 403

    app.dependency_overrides[auth_deps.get_current_user] = lambda: reviewer
    response_reviewer_review = client.post(f"/obligations/{obligation.id}/review", json=review_payload)
    assert response_reviewer_review.status_code == 200
    assert response_reviewer_review.json()["obligation"]["status"] == ReviewStatus.confirmed.value


def test_config_endpoints_require_admin_role():
    viewer = _make_user(UserRole.viewer)
    admin = _make_user(UserRole.admin)
    db = FakeSession(users=[viewer, admin])
    client = _build_client(db)
    app = client.app

    app.dependency_overrides[auth_deps.get_current_user] = lambda: viewer
    viewer_response = client.get("/config")
    assert viewer_response.status_code == 403

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin
    admin_response = client.get("/config")
    assert admin_response.status_code == 200


def test_admin_sees_only_own_assets():
    admin_a = _make_user(UserRole.admin)
    admin_b = _make_user(UserRole.admin)
    asset_a = _make_asset(created_by=admin_a.id)
    asset_b = _make_asset(created_by=admin_b.id)
    db = FakeSession(
        users=[admin_a, admin_b],
        assets=[asset_a, asset_b],
    )
    client = _build_client(db)
    app = client.app

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin_a
    response = client.get("/assets")
    assert response.status_code == 200
    items = response.json()["items"]
    asset_ids = [item["id"] for item in items]
    assert str(asset_a.id) in asset_ids
    assert str(asset_b.id) not in asset_ids

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin_b
    response = client.get("/assets")
    assert response.status_code == 200
    items = response.json()["items"]
    asset_ids = [item["id"] for item in items]
    assert str(asset_b.id) in asset_ids
    assert str(asset_a.id) not in asset_ids


def test_admin_cannot_access_another_admins_asset():
    admin_a = _make_user(UserRole.admin)
    admin_b = _make_user(UserRole.admin)
    asset_a = _make_asset(created_by=admin_a.id)
    db = FakeSession(
        users=[admin_a, admin_b],
        assets=[asset_a],
    )
    client = _build_client(db)
    app = client.app

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin_a
    response = client.get(f"/assets/{asset_a.id}")
    assert response.status_code == 200

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin_b
    response = client.get(f"/assets/{asset_a.id}")
    assert response.status_code == 403


def test_admin_obligations_scoped_to_own_assets():
    admin_a = _make_user(UserRole.admin)
    admin_b = _make_user(UserRole.admin)
    asset_a = _make_asset(created_by=admin_a.id)
    asset_b = _make_asset(created_by=admin_b.id)
    doc_a = _make_document(asset_id=asset_a.id, uploaded_by=admin_a.id)
    doc_b = _make_document(asset_id=asset_b.id, uploaded_by=admin_b.id)
    obligation_a = _make_obligation(doc_a.id)
    obligation_b = _make_obligation(doc_b.id)
    db = FakeSession(
        users=[admin_a, admin_b],
        assets=[asset_a, asset_b],
        documents=[doc_a, doc_b],
        obligations=[obligation_a, obligation_b],
    )
    client = _build_client(db)
    app = client.app

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin_a
    response = client.get("/obligations")
    assert response.status_code == 200
    items = response.json()["items"]
    obligation_ids = [item["id"] for item in items]
    assert str(obligation_a.id) in obligation_ids
    assert str(obligation_b.id) not in obligation_ids


def test_admin_risks_scoped_to_own_assets():
    admin_a = _make_user(UserRole.admin)
    admin_b = _make_user(UserRole.admin)
    asset_a = _make_asset(created_by=admin_a.id)
    asset_b = _make_asset(created_by=admin_b.id)
    doc_a = _make_document(asset_id=asset_a.id, uploaded_by=admin_a.id)
    doc_b = _make_document(asset_id=asset_b.id, uploaded_by=admin_b.id)
    risk_a = _make_risk(doc_a.id)
    risk_b = _make_risk(doc_b.id)
    db = FakeSession(
        users=[admin_a, admin_b],
        assets=[asset_a, asset_b],
        documents=[doc_a, doc_b],
        risks=[risk_a, risk_b],
    )
    client = _build_client(db)
    app = client.app

    app.dependency_overrides[auth_deps.get_current_user] = lambda: admin_a
    response = client.get("/risks")
    assert response.status_code == 200
    items = response.json()["items"]
    risk_ids = [item["id"] for item in items]
    assert str(risk_a.id) in risk_ids
    assert str(risk_b.id) not in risk_ids
