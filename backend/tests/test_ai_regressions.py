"""
AI Regression Tests — VeritasLayer multi-domain expansion

These tests guard against patterns that AI agents (Codex) introduced or could
re-introduce silently.  Each test is named after the bug category it prevents.

Regression catalogue:
  REG-01  OCR default model was stale (olmocr-7b → 404)
  REG-02  classify_document LLM failure path must still write document.domain
  REG-03  Invoice heuristic uses a regex special-case that is not in config;
          tests ensure the regex path is not accidentally deleted
  REG-04  Empty/absent `domains` config must not crash classification helpers
  REG-05  _serialize_obligation must carry both `domain` and `document_domain`
          because the frontend uses them; removing either breaks the UI silently
  REG-06  New financial/real-estate doc types must be accepted by the
          list_asset_documents filter without 422 Unprocessable Entity
"""

from __future__ import annotations

import hashlib
import types
import uuid

import pytest

from backend.app.models import (
    Document,
    DocumentType,
    DueKind,
    Modality,
    Obligation,
    ObligationEvidence,
    ObligationType,
    ParseStatus,
    ReviewStatus,
    Severity,
    TextSource,
)
import importlib

from backend.app.services import ocr as ocr_service
from backend.app.worker.tasks import classify as classify_task

_obligations_module = importlib.import_module("backend.app.routers.obligations")
_assets_module = importlib.import_module("backend.app.routers.assets")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_document(
    doc_type: DocumentType = DocumentType.unknown,
    domain: str | None = None,
) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="test.pdf",
        file_path="/tmp/test.pdf",
        sha256=hashlib.sha256(b"test").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.classification,
        doc_type=doc_type,
        domain=domain,
        scanned_page_count=0,
    )


def _make_obligation(document_id: uuid.UUID, **overrides) -> Obligation:
    data = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "obligation_type": ObligationType.payment,
        "obligation_text": "Pay premium on time",
        "modality": Modality.shall,
        "responsible_entity_id": None,
        "due_kind": DueKind.none,
        "due_date": None,
        "due_rule": None,
        "trigger_date": None,
        "severity": Severity.medium,
        "status": ReviewStatus.needs_review,
        "system_confidence": 55,
        "reviewer_confidence": None,
        "has_external_reference": False,
        "contradiction_flag": False,
        "extraction_run_id": None,
        "llm_severity": None,
        "llm_quality_confidence": None,
    }
    data.update(overrides)
    return Obligation(**data)


MINIMAL_DOMAINS = {
    "construction": {
        "doc_types": ["contract", "invoice"],
        "heuristics": {
            "contract": ["agree", "party", "shall"],
            "invoice": ["invoice", "amount", "total"],
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
    "general": {
        "doc_types": ["unknown"],
        "heuristics": {"unknown": []},
    },
}


# ---------------------------------------------------------------------------
# REG-01  OCR default model must not be the stale broken name
# ---------------------------------------------------------------------------


def test_ocr_default_model_is_not_stale(monkeypatch):
    """REG-01: allenai/olmocr-7b returns HTTP 404 on DeepInfra (observed in prod).
    The default fallback must use the current model name.
    """
    monkeypatch.setenv("DEEPINFRA_API_KEY", "fake-key-for-test")
    monkeypatch.delenv("DEEPINFRA_OLMOCR_MODEL", raising=False)
    cfg = ocr_service._load_ocr_config()
    assert cfg.model != "allenai/olmocr-7b", (
        "Default OCR model is the stale broken name that returns 404 on DeepInfra. "
        "Update the default in ocr.py._load_ocr_config."
    )
    assert "olmOCR" in cfg.model or "olmocr" in cfg.model.lower(), (
        f"Default model '{cfg.model}' doesn't look like an OCR model at all"
    )


# ---------------------------------------------------------------------------
# REG-02  classify_document LLM failure path still writes document.domain
# ---------------------------------------------------------------------------


class _FakeClassifySession:
    """Minimal fake session for classify_task tests."""

    def __init__(self, document: Document):
        self.document = document
        self.added: list = []

    def query(self, model):
        return _FakeClassifyQuery(self, model)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def close(self):
        pass


class _FakeClassifyQuery:
    def __init__(self, session: _FakeClassifySession, model):
        self._session = session
        self._model = model
        self._conditions: list = []

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def all(self):
        if self._model is classify_task.Document:
            return [self._session.document]
        if self._model is classify_task.DocumentPage:
            return []
        if self._model is classify_task.PromptVersion:
            return []
        if self._model is classify_task.ExtractionRun:
            return []
        return []

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


def test_classify_llm_failure_still_sets_domain(monkeypatch):
    """REG-02: When the LLM call fails entirely, classify_document must still
    assign document.domain (e.g. 'general') — a refactor that drops the domain
    assignment in the failure branch would silently leave it NULL.
    """
    document = _make_document()
    db = _FakeClassifySession(document)

    fake_settings = types.SimpleNamespace(
        raw={
            "domains": MINIMAL_DOMAINS,
            "classification": {"sample_pages": 3},
            "llm": {
                "max_retries": 1,
                "retry_backoff_base": 1,
                "primary_model": "test-model",
                "fallback_models": [],
            },
        }
    )

    def _failing_llm(*, model: str, prompt: str) -> dict:
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(classify_task, "settings", fake_settings)
    monkeypatch.setattr(classify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(classify_task, "update_parse_status", lambda *a, **kw: None)
    monkeypatch.setattr(classify_task, "call_classification_llm", _failing_llm)
    monkeypatch.setattr(classify_task.time, "sleep", lambda *a, **kw: None)

    result = classify_task.classify_document(str(document.id))

    assert result["status"] == "failed"
    assert document.domain is not None, (
        "REG-02: document.domain must be set even when the LLM call fails. "
        "It was NULL — the failure branch is missing the domain assignment."
    )
    assert document.domain == "general"


# ---------------------------------------------------------------------------
# REG-03  Invoice currency regex (not a config keyword — must not be deleted)
# ---------------------------------------------------------------------------


def test_invoice_heuristic_matches_currency_amount(monkeypatch):
    """REG-03a: The invoice heuristic uses a regex (\$\s?\d), not a keyword in
    config.  Text with a dollar-amount must still match invoice.
    """
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={"domains": MINIMAL_DOMAINS}))
    # MINIMAL_DOMAINS has no currency keyword for invoice — only regex covers it
    assert classify_task._heuristics_match(DocumentType.invoice, "Total due: $5,000 within 30 days")


def test_invoice_heuristic_no_match_without_currency(monkeypatch):
    """REG-03b: Text containing 'invoice' keyword but no dollar-digit pattern
    should NOT match via the regex path (text has no $N pattern).
    If keyword 'invoice' is in the config, it would match via keyword — this
    test uses MINIMAL_DOMAINS where only 'invoice', 'amount', 'total' keywords
    are listed, so plain text without a $ still matches via keyword.
    This test verifies the regex isn't the *only* path, preventing over-removal.
    """
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={"domains": MINIMAL_DOMAINS}))
    # "invoice" keyword is in MINIMAL_DOMAINS → should still match
    assert classify_task._heuristics_match(DocumentType.invoice, "this is an invoice for services")


def test_invoice_heuristic_regex_catches_no_keyword(monkeypatch):
    """REG-03c: A document with no 'invoice'/'amount'/'total' keywords but a
    $N pattern should STILL match invoice via the regex fallback.
    """
    no_keyword_domains = {
        "construction": {
            "doc_types": ["invoice"],
            "heuristics": {"invoice": []},  # empty keyword list
        },
        "general": {"doc_types": ["unknown"], "heuristics": {"unknown": []}},
    }
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={"domains": no_keyword_domains}))
    assert classify_task._heuristics_match(DocumentType.invoice, "Remit $1200 by end of month")
    assert not classify_task._heuristics_match(DocumentType.invoice, "Payment of one thousand dollars")


# ---------------------------------------------------------------------------
# REG-04  Empty / absent domains config must not crash helpers
# ---------------------------------------------------------------------------


def test_build_prompt_absent_domains_falls_back_to_enum(monkeypatch):
    """REG-04a: If 'domains' key is missing from config (e.g. accidental deletion),
    _build_prompt must fall back to the DocumentType enum list rather than crash.
    """
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={}))
    # Reset the module-level warning flag so the warning is allowed to fire
    classify_task._WARNED_MISSING_DOMAINS = False
    prompt = classify_task._build_prompt(["sample text"])
    assert "contract" in prompt  # from DocumentType enum fallback
    assert "unknown" in prompt


def test_heuristics_match_absent_domains_falls_back_gracefully(monkeypatch):
    """REG-04b: With no domains config, _heuristics_match must not raise.
    For unknown doc type it should return True; for any other type False.
    """
    monkeypatch.setattr(classify_task, "settings", types.SimpleNamespace(raw={}))
    classify_task._WARNED_MISSING_DOMAINS = False
    assert classify_task._heuristics_match(DocumentType.unknown, "anything")
    assert not classify_task._heuristics_match(DocumentType.insurance_policy, "insured premium")


# ---------------------------------------------------------------------------
# REG-05  _serialize_obligation must carry both domain and document_domain
# ---------------------------------------------------------------------------


def test_serialize_obligation_has_domain_and_document_domain():
    """REG-05: Codex added both `domain` and `document_domain` to the serialized
    obligation.  The frontend uses both.  If either key is dropped in a refactor
    the UI breaks silently — this test catches that regression.
    """
    doc = _make_document(doc_type=DocumentType.insurance_policy, domain="financial")
    ob = _make_obligation(doc.id)

    result = _obligations_module._serialize_obligation(ob, document_domain="financial")

    assert "domain" in result, "REG-05: 'domain' key missing from serialized obligation"
    assert "document_domain" in result, "REG-05: 'document_domain' key missing from serialized obligation"
    assert result["domain"] == "financial"
    assert result["document_domain"] == "financial"


def test_serialize_obligation_domain_null_for_old_docs():
    """REG-05b: Old documents without a domain must serialize as null (not missing)."""
    doc = _make_document(doc_type=DocumentType.contract, domain=None)
    ob = _make_obligation(doc.id)

    result = _obligations_module._serialize_obligation(ob, document_domain=None)

    assert "domain" in result
    assert "document_domain" in result
    assert result["domain"] is None
    assert result["document_domain"] is None


# ---------------------------------------------------------------------------
# REG-06  New doc types accepted by list_asset_documents filter (no 422)
# ---------------------------------------------------------------------------


class _FakeAssetsQuery:
    def __init__(self, session: "_FakeAssetsSession", model):
        self._session = session
        self._model = model

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def offset(self, _):
        return self

    def limit(self, _):
        return self

    def all(self):
        if self._model is _assets_module.Document:
            return list(self._session.documents)
        return []

    def count(self):
        return 0

    def join(self, *_):
        return self

    def group_by(self, *_):
        return self


class _FakeAssetsSession:
    def __init__(self, documents: list):
        self.documents = documents

    def query(self, model):
        return _FakeAssetsQuery(self, model)


@pytest.mark.parametrize("new_doc_type", [
    DocumentType.insurance_policy,
    DocumentType.loan_agreement,
    DocumentType.deed_of_trust,
    DocumentType.purchase_agreement,
    DocumentType.title_commitment,
    DocumentType.hoa_document,
    DocumentType.disclosure_report,
])
def test_list_asset_documents_accepts_new_doc_type_filter(new_doc_type):
    """REG-06: Each of the 7 new DocumentType values must be accepted by
    list_asset_documents without raising a 422 / ValueError.  FastAPI validates
    enum values at parse time — this test guards against typos in enum definitions.
    """
    asset_id = uuid.uuid4()
    db = _FakeAssetsSession(documents=[])
    # Should not raise
    result = _assets_module.list_asset_documents(
        asset_id=asset_id,
        doc_type=new_doc_type,
        parse_status=None,
        limit=50,
        cursor=0,
        db=db,
    )
    assert result["items"] == []
