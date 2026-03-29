from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import types
import uuid



from backend.app.models import (
    Document,
    DocumentPage,
    ExtractionRun,
    ExtractionStage,
    ExtractionStatus,
    PageProcessingStatus,
    ParseStatus,
    TextSource,
)
from backend.app.services.chunking import ChunkSlice
from backend.app.worker.tasks import chunk as chunk_task
from backend.app.worker.tasks import notify as notify_task
from backend.app.worker.tasks import ocr as ocr_task
from backend.app.worker.tasks import parse as parse_task


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return [row for row in self._rows_for_model() if self._matches_all(row)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.all())

    def delete(self, synchronize_session=False):
        if self._model is parse_task.TextSpan:
            self._session.spans = []
        elif self._model is parse_task.Chunk:
            self._session.chunks = []
        elif self._model is parse_task.DocumentPage:
            self._session.pages = []
        elif self._model is chunk_task.Chunk:
            self._session.chunks = []
        return 0

    def _rows_for_model(self):
        if self._model in (parse_task.Document, ocr_task.Document, chunk_task.Document, notify_task.Document):
            return [self._session.document] if self._session.document else []
        if self._model in (parse_task.DocumentPage, ocr_task.DocumentPage, chunk_task.DocumentPage, notify_task.DocumentPage):
            return list(self._session.pages)
        if self._model is notify_task.ExtractionRun:
            return list(self._session.extraction_runs)
        if self._model is parse_task.TextSpan:
            return list(self._session.spans)
        if self._model in (parse_task.Chunk, chunk_task.Chunk):
            return list(self._session.chunks)
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
    def __init__(self, document: Document | None = None, pages: list[DocumentPage] | None = None):
        self.document = document
        self.pages = pages or []
        self.spans = []
        self.chunks = []
        self.extraction_runs: list[ExtractionRun] = []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, Document):
            self.document = obj
            return
        if isinstance(obj, DocumentPage):
            if obj not in self.pages:
                self.pages.append(obj)
            return
        if isinstance(obj, parse_task.TextSpan):
            if obj not in self.spans:
                self.spans.append(obj)
            return
        if isinstance(obj, (parse_task.Chunk, chunk_task.Chunk)):
            if obj not in self.chunks:
                self.chunks.append(obj)
            return
        if isinstance(obj, ExtractionRun):
            if obj not in self.extraction_runs:
                self.extraction_runs.append(obj)
            return

    def commit(self):
        return None

    def rollback(self):
        return None

    def flush(self):
        return None

    def close(self):
        return None


def _make_document(source_name: str, file_path: str, parse_status: ParseStatus = ParseStatus.uploaded) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name=source_name,
        file_path=file_path,
        sha256=hashlib.sha256(source_name.encode("utf-8")).hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=parse_status,
        scanned_page_count=0,
    )


def _make_page(document_id: uuid.UUID, page_number: int, raw_text: str, status: PageProcessingStatus) -> DocumentPage:
    return DocumentPage(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=page_number,
        raw_text=raw_text,
        normalized_text="",
        text_source=TextSource.pdf_text,
        text_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        processing_status=status,
    )


def test_parse_document_parses_pdf_pages_and_counts_scanned(monkeypatch):
    document = _make_document("sample.pdf", "/tmp/sample.pdf")
    db = FakeSession(document=document, pages=[])

    class _FakePage:
        def __init__(self, text: str):
            self._text = text
            self.rect = types.SimpleNamespace(width=612, height=792)

        def get_text(self, mode: str):
            if mode == "text":
                return self._text
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "text": self._text.strip()[:10],
                                        "bbox": [0, 0, 50, 10],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }

    class _FakeDoc:
        page_count = 2

        def __init__(self):
            self.pages = [
                _FakePage("short"),
                _FakePage("This is a long enough page text to avoid scanned detection."),
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def load_page(self, idx: int):
            return self.pages[idx]

    monkeypatch.setattr(parse_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(parse_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(parse_task.fitz, "open", lambda *_args, **_kwargs: _FakeDoc())

    parse_task.parse_document(document.id)

    assert document.total_pages == 2
    assert document.scanned_page_count == 1
    assert len(db.pages) == 2
    assert all(page.processing_status == PageProcessingStatus.pending for page in db.pages)
    assert len(db.spans) >= 1


def test_parse_document_counts_failed_pages_for_ocr_fallback(monkeypatch):
    document = _make_document("partial-parse.pdf", "/tmp/partial-parse.pdf")
    db = FakeSession(document=document, pages=[])

    class _FakePage:
        def __init__(self, text: str):
            self._text = text
            self.rect = types.SimpleNamespace(width=612, height=792)

        def get_text(self, mode: str):
            if mode == "text":
                return self._text
            return {"blocks": []}

    class _FakeDoc:
        page_count = 2

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def load_page(self, idx: int):
            if idx == 1:
                raise ValueError("page parse failed")
            return _FakePage("This page has enough extractable text to avoid scanned detection.")

    monkeypatch.setattr(parse_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(parse_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(parse_task.fitz, "open", lambda *_args, **_kwargs: _FakeDoc())

    parse_task.parse_document(document.id)

    assert document.total_pages == 2
    assert document.scanned_page_count == 1
    assert len(db.pages) == 2
    assert any(page.processing_status == PageProcessingStatus.failed for page in db.pages)


def test_ocr_scanned_pages_isolates_page_failures(monkeypatch, tmp_path: Path):
    source_file = tmp_path / "scan.pdf"
    source_file.write_bytes(b"pdf-bytes")

    document = _make_document("scan.pdf", str(source_file), parse_status=ParseStatus.ocr)
    document.scanned_page_count = 2

    page1 = _make_page(document.id, 1, "tiny", PageProcessingStatus.pending)
    page2 = _make_page(document.id, 2, "mini", PageProcessingStatus.pending)
    db = FakeSession(document=document, pages=[page1, page2])

    def _fake_ocr(file_path: str, page_number: int) -> str:
        if page_number == 1:
            return "OCR SUCCESS"
        raise ocr_task.OCRUnavailableError("service down")

    monkeypatch.setattr(ocr_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(ocr_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ocr_task, "ocr_pdf_page", _fake_ocr)
    monkeypatch.setattr(ocr_task, "settings", types.SimpleNamespace(data_dir=str(tmp_path)))

    ocr_task.ocr_scanned_pages(document.id)

    assert page1.raw_text == "OCR SUCCESS"
    assert page1.text_source == TextSource.ocr
    assert page1.processing_status == PageProcessingStatus.pending

    assert page2.processing_status == PageProcessingStatus.failed
    assert page2.processing_error.startswith("ocr_failed:")

    assert document.processed_file_path is not None
    assert Path(document.processed_file_path).exists()


def test_normalize_pages_updates_processed_state(monkeypatch):
    document = _make_document("norm.pdf", "/tmp/norm.pdf", parse_status=ParseStatus.chunking)

    page1 = _make_page(document.id, 1, " A\tB\n\nC ", PageProcessingStatus.pending)
    page2 = _make_page(document.id, 2, "leave me", PageProcessingStatus.failed)
    page2.processing_error = "already failed"
    db = FakeSession(document=document, pages=[page1, page2])

    monkeypatch.setattr(chunk_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(chunk_task, "update_parse_status", lambda *_args, **_kwargs: None)

    chunk_task.normalize_pages(document.id)

    assert page1.normalized_text == "A B C"
    assert page1.processing_status == PageProcessingStatus.processed
    assert page1.text_sha256 == hashlib.sha256("A B C".encode("utf-8")).hexdigest()

    assert page2.processing_status == PageProcessingStatus.failed
    assert page2.processing_error == "already failed"


def test_chunk_pages_handles_page_level_failure(monkeypatch):
    document = _make_document("chunk.pdf", "/tmp/chunk.pdf", parse_status=ParseStatus.chunking)

    page1 = _make_page(document.id, 1, "unused", PageProcessingStatus.processed)
    page1.normalized_text = "ok payload"

    page2 = _make_page(document.id, 2, "unused", PageProcessingStatus.processed)
    page2.normalized_text = "boom payload"

    db = FakeSession(document=document, pages=[page1, page2])

    def _fake_split(text: str, max_chars: int):
        if text.startswith("boom"):
            raise ValueError("split error")
        return [ChunkSlice(char_start=0, char_end=2, text="ok", split_reason="full_page")]

    monkeypatch.setattr(chunk_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(chunk_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chunk_task, "split_text_into_chunks", _fake_split)
    monkeypatch.setattr(chunk_task, "settings", types.SimpleNamespace(raw={"chunking": {"max_chars": 4000}}))

    chunk_task.chunk_pages(document.id)

    assert len(db.chunks) == 1
    assert db.chunks[0].page_number == 1
    assert page2.processing_status == PageProcessingStatus.failed
    assert page2.processing_error.startswith("chunk_failed:")


def test_persist_final_status_sets_complete_or_partial(monkeypatch):
    doc_complete = _make_document("done.pdf", "/tmp/done.pdf", parse_status=ParseStatus.scoring)
    pages_complete = [_make_page(doc_complete.id, 1, "ok", PageProcessingStatus.processed)]
    db_complete = FakeSession(document=doc_complete, pages=pages_complete)

    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db_complete)
    notify_task.persist_final_status(doc_complete.id)
    assert doc_complete.parse_status == ParseStatus.complete

    doc_partial = _make_document("partial.pdf", "/tmp/partial.pdf", parse_status=ParseStatus.scoring)
    pages_partial = [_make_page(doc_partial.id, 1, "bad", PageProcessingStatus.failed)]
    db_partial = FakeSession(document=doc_partial, pages=pages_partial)

    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db_partial)
    notify_task.persist_final_status(doc_partial.id)
    assert doc_partial.parse_status == ParseStatus.partially_processed


def test_persist_final_status_marks_partial_on_failed_extraction(monkeypatch):
    document = _make_document("extract-fail.pdf", "/tmp/extract-fail.pdf", parse_status=ParseStatus.scoring)
    pages = [_make_page(document.id, 1, "ok", PageProcessingStatus.processed)]
    db = FakeSession(document=document, pages=pages)
    db.extraction_runs.append(
        ExtractionRun(
            id=uuid.uuid4(),
            document_id=document.id,
            prompt_version_id=uuid.uuid4(),
            model_used="test-model",
            config_snapshot={},
            stage=ExtractionStage.obligation_extraction,
            status=ExtractionStatus.failed,
        )
    )

    monkeypatch.setattr(notify_task, "SessionLocal", lambda: db)
    notify_task.persist_final_status(document.id)

    assert document.parse_status == ParseStatus.partially_processed
