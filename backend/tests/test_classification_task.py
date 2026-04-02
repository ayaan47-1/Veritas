from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import sys
import types
import uuid



from backend.app.models import (
    Document,
    DocumentPage,
    DocumentType,
    ExtractionRun,
    ExtractionStage,
    ExtractionStatus,
    ParseStatus,
    PromptVersion,
    TextSource,
)
from backend.app.worker.tasks import classify as classify_task


MINIMAL_DOMAINS = {
    "construction": {
        "doc_types": ["contract", "rfi", "invoice"],
        "heuristics": {
            "contract": ["agree", "party", "shall"],
            "rfi": ["request for information", "clarification", "rfi"],
            "invoice": ["invoice", "amount", "total", "usd"],
        },
    },
    "financial": {
        "doc_types": ["insurance_policy", "loan_agreement", "deed_of_trust"],
        "heuristics": {
            "insurance_policy": ["insured", "premium", "coverage"],
            "loan_agreement": ["borrower", "lender", "promissory"],
            "deed_of_trust": ["trustor", "deed of trust", "mortgage"],
        },
    },
    "real_estate": {
        "doc_types": ["purchase_agreement"],
        "heuristics": {
            "purchase_agreement": ["purchase price", "buyer", "closing"],
        },
    },
    "general": {
        "doc_types": ["unknown"],
        "heuristics": {"unknown": []},
    },
}


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

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def all(self):
        return [row for row in self._rows_for_model() if self._matches_all(row)]

    def _rows_for_model(self):
        if self._model is classify_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is classify_task.DocumentPage:
            return list(self._session.pages)
        if self._model is classify_task.PromptVersion:
            return list(self._session.prompt_versions)
        if self._model is classify_task.ExtractionRun:
            return list(self._session.extraction_runs)
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
    def __init__(self, document: Document, pages: list[DocumentPage]):
        self.document = document
        self.pages = pages
        self.prompt_versions: list[PromptVersion] = []
        self.extraction_runs: list[ExtractionRun] = []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, Document):
            self.document = obj
            return
        if isinstance(obj, PromptVersion):
            if obj not in self.prompt_versions:
                self.prompt_versions.append(obj)
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


def _make_document() -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="contract.pdf",
        file_path="/tmp/contract.pdf",
        sha256=hashlib.sha256(b"contract").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.classification,
        doc_type=DocumentType.unknown,
        scanned_page_count=0,
    )


def _make_page(document_id: uuid.UUID, page_number: int, text: str) -> DocumentPage:
    return DocumentPage(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=page_number,
        raw_text=text,
        normalized_text=text,
        text_source=TextSource.pdf_text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def test_heuristics_match_loads_from_config(monkeypatch):
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={"domains": MINIMAL_DOMAINS}))
    assert classify_task._heuristics_match(DocumentType.insurance_policy, "The insured must pay the premium.")
    assert not classify_task._heuristics_match(DocumentType.insurance_policy, "This is a lease agreement.")


def test_heuristics_match_unknown_always_true(monkeypatch):
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={"domains": MINIMAL_DOMAINS}))
    assert classify_task._heuristics_match(DocumentType.unknown, "any text at all")


def test_build_prompt_includes_all_configured_doc_types(monkeypatch):
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={"domains": MINIMAL_DOMAINS}))
    prompt = classify_task._build_prompt(["sample page text"])
    assert "insurance_policy" in prompt
    assert "loan_agreement" in prompt
    assert "purchase_agreement" in prompt


def test_domain_derived_and_stored_after_classify(monkeypatch):
    document = _make_document()
    pages = [_make_page(document.id, 1, "The deed of trust encumbers the property.")]
    db = FakeSession(document=document, pages=pages)

    fake_settings = types.SimpleNamespace(
        raw={
            "domains": MINIMAL_DOMAINS,
            "classification": {"sample_pages": 3},
            "llm": {"max_retries": 1, "retry_backoff_base": 1, "primary_model": "test-model", "fallback_models": []},
        }
    )

    def _fake_llm(*, model: str, prompt: str) -> dict:
        return {"doc_type": "deed_of_trust", "confidence": 0.9, "explanation": "test"}

    monkeypatch.setattr(classify_task, "settings", fake_settings)
    monkeypatch.setattr(classify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(classify_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(classify_task, "call_classification_llm", _fake_llm)
    monkeypatch.setattr(classify_task.time, "sleep", lambda *_args, **_kwargs: None)

    classify_task.classify_document(document.id)

    assert document.domain == "financial"


def test_unknown_doc_type_maps_to_general_domain(monkeypatch):
    document = _make_document()
    pages = [_make_page(document.id, 1, "Some random memo text.")]
    db = FakeSession(document=document, pages=pages)

    fake_settings = types.SimpleNamespace(
        raw={
            "domains": MINIMAL_DOMAINS,
            "classification": {"sample_pages": 3},
            "llm": {"max_retries": 1, "retry_backoff_base": 1, "primary_model": "test-model", "fallback_models": []},
        }
    )

    def _fake_llm(*, model: str, prompt: str) -> dict:
        return {"doc_type": "unknown", "confidence": 0.5, "explanation": "test"}

    monkeypatch.setattr(classify_task, "settings", fake_settings)
    monkeypatch.setattr(classify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(classify_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(classify_task, "call_classification_llm", _fake_llm)
    monkeypatch.setattr(classify_task.time, "sleep", lambda *_args, **_kwargs: None)

    classify_task.classify_document(document.id)

    assert document.domain == "general"


def test_classification_sets_doc_type_when_heuristics_agree(monkeypatch):
    document = _make_document()
    pages = [_make_page(document.id, 1, "The parties agree that the contractor shall perform all obligations.")]
    db = FakeSession(document=document, pages=pages)

    def _fake_llm(*, model: str, prompt: str) -> dict:
        return {"doc_type": "contract", "confidence": 0.92, "explanation": "contains contract clauses"}

    monkeypatch.setattr(classify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(classify_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(classify_task, "call_classification_llm", _fake_llm)
    monkeypatch.setattr(classify_task.time, "sleep", lambda *_args, **_kwargs: None)

    classify_task.classify_document(document.id)

    assert document.doc_type == DocumentType.contract
    assert document.doc_type_confidence == 0.92
    assert len(db.extraction_runs) == 1
    run = db.extraction_runs[0]
    assert run.stage == ExtractionStage.classification
    assert run.status == ExtractionStatus.completed
    assert run.completed_at is not None


def test_classification_marks_unknown_when_heuristics_disagree(monkeypatch):
    document = _make_document()
    pages = [_make_page(document.id, 1, "Meeting notes about site logistics.")]
    db = FakeSession(document=document, pages=pages)

    def _fake_llm(*, model: str, prompt: str) -> dict:
        return {"doc_type": "invoice", "confidence": 0.88, "explanation": "looks like invoice"}

    monkeypatch.setattr(classify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(classify_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(classify_task, "call_classification_llm", _fake_llm)
    monkeypatch.setattr(classify_task.time, "sleep", lambda *_args, **_kwargs: None)

    classify_task.classify_document(document.id)

    assert document.doc_type == DocumentType.unknown
    assert len(db.extraction_runs) == 1
    assert db.extraction_runs[0].status == ExtractionStatus.completed


def test_classification_uses_fallback_model_and_records_run(monkeypatch):
    document = _make_document()
    pages = [_make_page(document.id, 1, "Request for information regarding schedule dependency.")]
    db = FakeSession(document=document, pages=pages)

    calls = []

    def _fake_llm(*, model: str, prompt: str) -> dict:
        calls.append(model)
        if model == "primary-model":
            raise RuntimeError("primary failed")
        return {"doc_type": "rfi", "confidence": 0.67, "explanation": "contains request for information"}

    fake_settings = types.SimpleNamespace(
        raw={
            "domains": MINIMAL_DOMAINS,
            "llm": {
                "primary_model": "primary-model",
                "fallback_models": ["fallback-model"],
                "max_retries": 1,
                "retry_backoff_base": 1,
            },
            "classification": {"sample_pages": 3},
        }
    )

    monkeypatch.setattr(classify_task, "settings", fake_settings)
    monkeypatch.setattr(classify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(classify_task, "update_parse_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(classify_task, "call_classification_llm", _fake_llm)
    monkeypatch.setattr(classify_task.time, "sleep", lambda *_args, **_kwargs: None)

    classify_task.classify_document(document.id)

    assert calls == ["primary-model", "fallback-model"]
    assert document.doc_type == DocumentType.rfi
    assert len(db.extraction_runs) == 1
    assert db.extraction_runs[0].model_used == "fallback-model"
    assert db.extraction_runs[0].status == ExtractionStatus.completed
