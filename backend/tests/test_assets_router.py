from __future__ import annotations

import hashlib
import importlib
import sys
import types
import uuid

from backend.app.models import Document, DocumentType, ParseStatus


if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")

    class _DummyCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def autodiscover_tasks(self, *args, **kwargs) -> None:
            return None

        def task(self, *args, **kwargs):
            def _decorator(func):
                return func

            return _decorator

    celery_module.Celery = _DummyCelery
    sys.modules["celery"] = celery_module


assets_router = importlib.import_module("backend.app.routers.assets")


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
        if self._model is assets_router.Document:
            return list(self._session.documents)
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
    def __init__(self, documents: list[Document]):
        self.documents = documents

    def query(self, model):
        return FakeQuery(self, model)


def _make_document(asset_id: uuid.UUID, source_name: str, doc_type: DocumentType, parse_status: ParseStatus) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        source_name=source_name,
        file_path=f"/tmp/{source_name}",
        sha256=hashlib.sha256(source_name.encode("utf-8")).hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        doc_type=doc_type,
        parse_status=parse_status,
        scanned_page_count=0,
    )


def test_list_asset_documents_filters_and_paginates():
    asset_id = uuid.uuid4()
    other_asset = uuid.uuid4()
    docs = [
        _make_document(asset_id, "a.pdf", DocumentType.contract, ParseStatus.complete),
        _make_document(asset_id, "b.pdf", DocumentType.invoice, ParseStatus.complete),
        _make_document(asset_id, "c.pdf", DocumentType.contract, ParseStatus.failed),
        _make_document(other_asset, "d.pdf", DocumentType.contract, ParseStatus.complete),
    ]
    db = FakeSession(documents=docs)

    result = assets_router.list_asset_documents(
        asset_id=asset_id,
        doc_type=DocumentType.contract,
        parse_status=ParseStatus.complete,
        limit=1,
        cursor=0,
        db=db,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["source_name"] == "a.pdf"
    assert result["next_cursor"] is None
