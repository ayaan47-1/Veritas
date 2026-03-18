from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
import sys
import types
import uuid

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend.app.models import Document, DocumentPage, PageProcessingStatus, ParseStatus, TextSource, TextSpan


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


documents_router = importlib.import_module("backend.app.routers.documents")


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        return [row for row in self._rows_for_model() if self._matches_all(row)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def _rows_for_model(self):
        if self._model is documents_router.Document:
            return [self._session.document] if self._session.document else []
        if self._model is documents_router.DocumentPage:
            return list(self._session.pages)
        if self._model is documents_router.TextSpan:
            return list(self._session.spans)
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
        pages: list[DocumentPage] | None = None,
        spans: list[TextSpan] | None = None,
    ):
        self.document = document
        self.pages = pages or []
        self.spans = spans or []

    def query(self, model):
        return FakeQuery(self, model)


def _make_document(file_path: str, processed_file_path: str | None = None) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="doc.pdf",
        file_path=file_path,
        processed_file_path=processed_file_path,
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


def _make_page(document_id: uuid.UUID, page_number: int, raw_text: str, normalized_text: str) -> DocumentPage:
    return DocumentPage(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=page_number,
        raw_text=raw_text,
        normalized_text=normalized_text,
        text_source=TextSource.pdf_text,
        text_sha256=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        processing_status=PageProcessingStatus.processed,
    )


def _make_span(document_id: uuid.UUID, page_number: int, start: int, end: int, text: str) -> TextSpan:
    return TextSpan(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=page_number,
        char_start=start,
        char_end=end,
        bbox_x1=0.0,
        bbox_y1=0.0,
        bbox_x2=10.0,
        bbox_y2=10.0,
        span_text=text,
        span_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def test_get_document_page_returns_text_and_spans():
    document = _make_document("/tmp/original.pdf")
    page = _make_page(document.id, 2, "Raw Page Text", "raw page text")
    span = _make_span(document.id, 2, 0, 3, "Raw")
    db = FakeSession(document=document, pages=[page], spans=[span])

    payload = documents_router.get_document_page(document.id, 2, db=db)

    assert payload["document_id"] == str(document.id)
    assert payload["page_number"] == 2
    assert payload["raw_text"] == "Raw Page Text"
    assert payload["normalized_text"] == "raw page text"
    assert len(payload["text_spans"]) == 1
    assert payload["text_spans"][0]["char_start"] == 0


def test_get_document_page_raises_404_for_missing_page():
    document = _make_document("/tmp/original.pdf")
    db = FakeSession(document=document, pages=[], spans=[])

    with pytest.raises(HTTPException) as exc:
        documents_router.get_document_page(document.id, 99, db=db)

    assert exc.value.status_code == 404


def test_get_document_pdf_prefers_processed_when_available(tmp_path: Path):
    original = tmp_path / "original.pdf"
    processed = tmp_path / "processed.pdf"
    original.write_bytes(b"original")
    processed.write_bytes(b"processed")

    document = _make_document(str(original), str(processed))
    db = FakeSession(document=document)

    response = documents_router.get_document_pdf(document.id, processed=True, db=db)

    assert isinstance(response, FileResponse)
    assert response.path == str(processed)


def test_get_document_pdf_returns_original_when_processed_disabled(tmp_path: Path):
    original = tmp_path / "original.pdf"
    processed = tmp_path / "processed.pdf"
    original.write_bytes(b"original")
    processed.write_bytes(b"processed")

    document = _make_document(str(original), str(processed))
    db = FakeSession(document=document)

    response = documents_router.get_document_pdf(document.id, processed=False, db=db)

    assert isinstance(response, FileResponse)
    assert response.path == str(original)


def test_get_document_pdf_raises_404_when_selected_file_missing(tmp_path: Path):
    missing = tmp_path / "missing.pdf"
    document = _make_document(str(missing), None)
    db = FakeSession(document=document)

    with pytest.raises(HTTPException) as exc:
        documents_router.get_document_pdf(document.id, processed=False, db=db)

    assert exc.value.status_code == 404
