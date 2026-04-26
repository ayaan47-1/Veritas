from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.database import get_db
from backend.app.main import create_app
from backend.app.models import (
    Document,
    OIDCProvider,
    ParseStatus,
    User,
    UserAssetAssignment,
    UserRole,
)


ASSET_ID = uuid.uuid4()
OTHER_ASSET_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def all(self):
        rows = list(self._rows_for_model())
        for condition in self._conditions:
            rows = [row for row in rows if self._matches(row, condition)]
        return rows

    def _rows_for_model(self):
        if self._model is User:
            return self._session.users
        if self._model is UserAssetAssignment:
            return self._session.assignments
        if self._model is Document:
            return self._session.documents
        return []

    def _matches(self, row, condition):
        left = getattr(condition, "left", None)
        right = getattr(condition, "right", None)
        key = getattr(left, "key", None) if left is not None else None
        if key is None:
            return True
        value = getattr(right, "value", right)
        return getattr(row, key, None) == value


class FakeSavepoint:
    def __init__(self, session: "FakeSession"):
        self._session = session
        self._document_count = len(session.documents)

    def commit(self):
        return None

    def rollback(self):
        del self._session.documents[self._document_count :]


class FakeSession:
    def __init__(
        self,
        *,
        user_role: UserRole = UserRole.reviewer,
        assignments: list[UserAssetAssignment] | None = None,
        documents: list[Document] | None = None,
    ):
        self.users = [
            User(
                id=USER_ID,
                oidc_provider=OIDCProvider.clerk,
                oidc_subject="clerk-user",
                email="reviewer@example.com",
                name="Reviewer",
                role=user_role,
                is_active=True,
                last_login_at=datetime.now(UTC),
            )
        ]
        self.assignments = assignments if assignments is not None else [
            UserAssetAssignment(id=uuid.uuid4(), user_id=USER_ID, asset_id=ASSET_ID)
        ]
        self.documents = documents or []
        self.commit_count = 0
        self.rollback_count = 0

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, Document) and obj not in self.documents:
            self.documents.append(obj)
        if isinstance(obj, User) and obj not in self.users:
            self.users.append(obj)

    def begin_nested(self):
        return FakeSavepoint(self)

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        return None


def _client(session: FakeSession) -> TestClient:
    app = create_app()

    def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _pdf(name: str, body: bytes):
    return ("files", (name, io.BytesIO(body), "application/pdf"))


def _post_bulk(client: TestClient, files, asset_id: uuid.UUID = ASSET_ID):
    with (
        patch("backend.app.auth.deps.verify_clerk_token", return_value={"sub": "clerk-user", "email": "reviewer@example.com"}),
        patch("backend.app.routers.ingest.LocalStorage") as mock_storage,
        patch("backend.app.routers.ingest.fitz") as mock_fitz,
        patch("backend.app.routers.ingest.inngest_client.send", new_callable=AsyncMock) as mock_send,
    ):
        mock_storage.return_value.save.side_effect = lambda relative_path, _content: f"/tmp/{relative_path}"
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda self, *a: None
        mock_fitz.open.return_value = mock_doc

        response = client.post(
            "/ingest/bulk",
            data={"asset_id": str(asset_id), "uploaded_by": str(USER_ID)},
            files=files,
            headers={"Authorization": "Bearer test-token"},
        )

    return response, mock_send


def test_bulk_success_path_with_three_valid_pdfs():
    session = FakeSession()
    client = _client(session)

    response, mock_send = _post_bulk(
        client,
        [
            _pdf("lease1.pdf", b"%PDF-1 lease1"),
            _pdf("lease2.pdf", b"%PDF-1 lease2"),
            _pdf("lease3.pdf", b"%PDF-1 lease3"),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert [row["filename"] for row in payload["succeeded"]] == ["lease1.pdf", "lease2.pdf", "lease3.pdf"]
    assert payload["failed"] == []
    assert len(session.documents) == 3
    assert mock_send.await_count == 3


def test_bulk_partial_failure_continues_processing_valid_files():
    session = FakeSession()
    client = _client(session)

    response, mock_send = _post_bulk(
        client,
        [
            _pdf("lease1.pdf", b"%PDF-1 lease1"),
            ("files", ("notes.txt", io.BytesIO(b"not a pdf"), "text/plain")),
            _pdf("lease3.pdf", b"%PDF-1 lease3"),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert [row["filename"] for row in payload["succeeded"]] == ["lease1.pdf", "lease3.pdf"]
    assert payload["failed"] == [{"filename": "notes.txt", "reason": "Only PDF uploads are supported"}]
    assert len(session.documents) == 2
    assert mock_send.await_count == 2


def test_bulk_all_invalid_returns_400():
    session = FakeSession()
    client = _client(session)

    response, mock_send = _post_bulk(
        client,
        [
            ("files", ("notes.txt", io.BytesIO(b"not a pdf"), "text/plain")),
            ("files", ("image.png", io.BytesIO(b"png"), "image/png")),
        ],
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["succeeded"] == []
    assert len(payload["failed"]) == 2
    assert len(session.documents) == 0
    assert mock_send.await_count == 0


def test_bulk_dedup_returns_existing_document_id_for_same_asset_sha256():
    body = b"%PDF-1 duplicate"
    existing = Document(
        id=uuid.uuid4(),
        asset_id=ASSET_ID,
        source_name="existing.pdf",
        file_path="/tmp/existing.pdf",
        processed_file_path=None,
        sha256=hashlib.sha256(body).hexdigest(),
        mime_type="application/pdf",
        uploaded_by=USER_ID,
        parse_status=ParseStatus.complete,
        total_pages=1,
        scanned_page_count=0,
    )
    session = FakeSession(documents=[existing])
    client = _client(session)

    response, mock_send = _post_bulk(client, [_pdf("duplicate.pdf", body)])

    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] == [{"filename": "duplicate.pdf", "document_id": str(existing.id)}]
    assert payload["failed"] == []
    assert len(session.documents) == 1
    assert mock_send.await_count == 0


def test_bulk_unauthorized_asset_returns_403():
    session = FakeSession(assignments=[
        UserAssetAssignment(id=uuid.uuid4(), user_id=USER_ID, asset_id=OTHER_ASSET_ID)
    ])
    client = _client(session)

    response, mock_send = _post_bulk(client, [_pdf("lease.pdf", b"%PDF-1 lease")])

    assert response.status_code == 403
    assert response.json()["detail"] == "No access to this asset"
    assert len(session.documents) == 0
    assert mock_send.await_count == 0
