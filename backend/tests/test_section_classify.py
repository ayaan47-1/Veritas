"""Tests for section classification (stage 5b)."""
from __future__ import annotations

import hashlib
import types
import uuid

from backend.app.models import (
    Chunk,
    Document,
    DocumentType,
    ParseStatus,
    SplitReason,
)
from backend.app.worker.tasks import section_classify as sc_task


def _make_document() -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.extraction,
        doc_type=DocumentType.lease,
        scanned_page_count=0,
    )


def _make_chunk(document_id: uuid.UUID, page: int, text: str) -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=page,
        char_start=0,
        char_end=len(text),
        text=text,
        chunk_sha256=hashlib.sha256(text.encode()).hexdigest(),
        split_reason=SplitReason.full_page,
    )


class FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def order_by(self, *args):
        return self

    def all(self):
        if self._model is sc_task.Chunk:
            return list(self._session.chunks)
        return []

    def first(self):
        if self._model is sc_task.Document:
            return self._session.document
        rows = self.all()
        return rows[0] if rows else None


class FakeSession:
    def __init__(self, document, chunks):
        self.document = document
        self.chunks = chunks

    def query(self, model):
        return FakeQuery(self, model)

    def commit(self):
        pass

    def close(self):
        pass


def _settings(extra=None):
    raw = {"section_classification": {"model": "claude-haiku-4-5-20251001"}}
    if extra:
        raw.update(extra)
    return types.SimpleNamespace(raw=raw)


def test_build_section_classify_prompt_numbers_sections():
    doc = _make_document()
    c1 = _make_chunk(doc.id, 1, "Tenant shall pay rent.")
    c2 = _make_chunk(doc.id, 5, "Disclosure of lead paint.")
    prompt = sc_task._build_section_classify_prompt([c1, c2])
    assert "Section 1 (Page 1)" in prompt
    assert "Section 2 (Page 5)" in prompt
    assert "Tenant shall pay rent." in prompt
    assert "Disclosure of lead paint." in prompt


def test_classify_chunk_sections_labels_chunks(monkeypatch):
    doc = _make_document()
    c1 = _make_chunk(doc.id, 1, "Tenant shall pay rent monthly.")
    c2 = _make_chunk(doc.id, 2, "Statutory disclosure of tenant rights.")
    c3 = _make_chunk(doc.id, 3, "Landlord may terminate lease for cause.")
    db = FakeSession(doc, [c1, c2, c3])

    fake_response = [
        {"section": 1, "label": "agreement_body"},
        {"section": 2, "label": "non_agreement"},
        {"section": 3, "label": "agreement_body"},
    ]

    monkeypatch.setattr(sc_task, "settings", _settings())
    monkeypatch.setattr(sc_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(sc_task, "update_parse_status", lambda *a, **k: None)
    monkeypatch.setattr(sc_task, "call_section_classify_llm", lambda **kw: fake_response)

    result = sc_task.classify_chunk_sections(str(doc.id))

    assert result["status"] == "ok"
    assert result["agreement_body"] == 2
    assert result["non_agreement"] == 1
    assert c1.section_label == "agreement_body"
    assert c2.section_label == "non_agreement"
    assert c3.section_label == "agreement_body"


def test_classify_chunk_sections_defaults_to_agreement_body_on_missing_label(monkeypatch):
    doc = _make_document()
    c1 = _make_chunk(doc.id, 1, "Clause text.")
    c2 = _make_chunk(doc.id, 2, "More text.")
    db = FakeSession(doc, [c1, c2])

    # Only return label for section 1, section 2 missing
    fake_response = [{"section": 1, "label": "agreement_body"}]

    monkeypatch.setattr(sc_task, "settings", _settings())
    monkeypatch.setattr(sc_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(sc_task, "update_parse_status", lambda *a, **k: None)
    monkeypatch.setattr(sc_task, "call_section_classify_llm", lambda **kw: fake_response)

    result = sc_task.classify_chunk_sections(str(doc.id))

    assert result["agreement_body"] == 2  # missing label defaults to agreement_body
    assert result["non_agreement"] == 0
    assert c2.section_label == "agreement_body"


def test_classify_chunk_sections_fallback_on_llm_failure(monkeypatch):
    doc = _make_document()
    c1 = _make_chunk(doc.id, 1, "Some text.")
    db = FakeSession(doc, [c1])

    def _fail(**kw):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(sc_task, "settings", _settings())
    monkeypatch.setattr(sc_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(sc_task, "update_parse_status", lambda *a, **k: None)
    monkeypatch.setattr(sc_task, "call_section_classify_llm", _fail)

    result = sc_task.classify_chunk_sections(str(doc.id))

    assert result["status"] == "fallback"
    assert result["agreement_body"] == 1
    assert c1.section_label == "agreement_body"


def test_prompt_truncates_long_chunks():
    doc = _make_document()
    long_text = "x" * 1000
    c1 = _make_chunk(doc.id, 1, long_text)
    prompt = sc_task._build_section_classify_prompt([c1])
    # Only first 500 chars should appear
    assert "x" * 500 in prompt
    assert "x" * 501 not in prompt
