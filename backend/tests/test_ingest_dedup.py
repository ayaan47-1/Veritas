"""Test that SHA256 duplicate check is scoped to asset_id, not global."""
from __future__ import annotations

import hashlib
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from backend.app.database import get_db
from backend.app.main import create_app
from backend.app.models import Document, ParseStatus


PDF_HEADER = b"%PDF-1.4 fake"
SHA_SAME = hashlib.sha256(PDF_HEADER).hexdigest()

ASSET_A = uuid.uuid4()
ASSET_B = uuid.uuid4()
USER_ID = uuid.uuid4()


def _existing_doc(asset_id: uuid.UUID) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        source_name="existing.pdf",
        file_path="/tmp/existing.pdf",
        sha256=SHA_SAME,
        mime_type="application/pdf",
        uploaded_by=USER_ID,
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


class FakeQuery:
    def __init__(self, docs: list[Document]):
        self._docs = docs
        self._conditions: list = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def first(self):
        rows = list(self._docs)
        for cond in self._conditions:
            left = getattr(cond, "left", None)
            right = getattr(cond, "right", None)
            key = getattr(left, "key", None) if left is not None else None
            val = getattr(right, "value", right) if right is not None else None
            if key is not None:
                rows = [r for r in rows if getattr(r, key, None) == val]
        return rows[0] if rows else None


class FakeSession:
    def __init__(self, docs: list[Document]):
        self._docs = docs

    def query(self, model):
        if model is Document:
            return FakeQuery(self._docs)
        return FakeQuery([])

    def add(self, _obj):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _make_client(docs: list[Document]) -> TestClient:
    app = create_app()
    db = FakeSession(docs)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _upload(client: TestClient, asset_id: uuid.UUID) -> int:
    with patch("backend.app.routers.ingest.fitz") as mock_fitz:
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda self, *a: None
        mock_fitz.open.return_value = mock_doc

        with patch("backend.app.routers.ingest.LocalStorage") as mock_storage:
            mock_storage.return_value.save.return_value = "/tmp/saved.pdf"

            resp = client.post(
                "/ingest",
                data={
                    "asset_id": str(asset_id),
                    "uploaded_by": str(USER_ID),
                },
                files={"file": ("test.pdf", io.BytesIO(PDF_HEADER), "application/pdf")},
            )
    return resp.status_code


def test_same_sha256_different_asset_allowed():
    """Same file uploaded to two different assets should be allowed."""
    existing = _existing_doc(ASSET_A)
    client = _make_client([existing])
    status = _upload(client, ASSET_B)
    assert status == 201, "Upload to a different asset should succeed"


def test_same_sha256_same_asset_rejected():
    """Same file uploaded to the same asset should be rejected as duplicate."""
    existing = _existing_doc(ASSET_A)
    client = _make_client([existing])
    status = _upload(client, ASSET_A)
    assert status == 409, "Upload to the same asset should be rejected"
