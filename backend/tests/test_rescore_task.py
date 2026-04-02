from __future__ import annotations

import hashlib
import json
import types
import uuid

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
    Risk,
    RiskEvidence,
    RiskType,
    Severity,
    TextSource,
)
from backend.app.worker.tasks import rescore as rescore_task


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def all(self):
        return [row for row in self._rows_for_model() if self._matches_all(row)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def _rows_for_model(self):
        if self._model is rescore_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is rescore_task.Obligation:
            return list(self._session.obligations)
        if self._model is rescore_task.Risk:
            return list(self._session.risks)
        if self._model is rescore_task.ObligationEvidence:
            return list(self._session.obligation_evidence)
        if self._model is rescore_task.RiskEvidence:
            return list(self._session.risk_evidence)
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
        document: Document,
        obligations: list[Obligation] | None = None,
        risks: list[Risk] | None = None,
        obligation_evidence: list[ObligationEvidence] | None = None,
        risk_evidence: list[RiskEvidence] | None = None,
    ):
        self.document = document
        self.obligations = obligations or []
        self.risks = risks or []
        self.obligation_evidence = obligation_evidence or []
        self.risk_evidence = risk_evidence or []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        return None

    def commit(self):
        return None

    def close(self):
        return None


def _make_document(doc_type: DocumentType = DocumentType.contract, domain: str | None = None) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="test.pdf",
        file_path="/tmp/test.pdf",
        sha256=hashlib.sha256(b"test").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.scoring,
        doc_type=doc_type,
        domain=domain,
        scanned_page_count=0,
    )


def _make_obligation(document_id: uuid.UUID, **overrides) -> Obligation:
    data = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "obligation_type": ObligationType.payment,
        "obligation_text": "Pay 10% retainage",
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


def _make_risk(document_id: uuid.UUID, **overrides) -> Risk:
    data = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "risk_type": RiskType.financial,
        "risk_text": "Penalty for non-compliance",
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
    return Risk(**data)


def _make_evidence(document_id, item_id, is_risk=False):
    cls = RiskEvidence if is_risk else ObligationEvidence
    id_field = "risk_id" if is_risk else "obligation_id"
    return cls(
        id=uuid.uuid4(),
        **{id_field: item_id},
        document_id=document_id,
        page_number=3,
        quote="Relevant quote text",
        quote_sha256=hashlib.sha256(b"quote").hexdigest(),
        raw_char_start=0,
        raw_char_end=20,
        normalized_char_start=0,
        normalized_char_end=20,
        source=TextSource.pdf_text,
    )


def test_rescore_updates_llm_severity_and_quality_confidence(monkeypatch):
    doc = _make_document()
    ob = _make_obligation(doc.id, severity=Severity.medium, system_confidence=55)
    risk = _make_risk(doc.id, severity=Severity.medium, system_confidence=55)
    ob_ev = _make_evidence(doc.id, ob.id)
    risk_ev = _make_evidence(doc.id, risk.id, is_risk=True)
    db = FakeSession(
        document=doc,
        obligations=[ob],
        risks=[risk],
        obligation_evidence=[ob_ev],
        risk_evidence=[risk_ev],
    )

    def _fake_llm_completion(model: str, prompt: str) -> str:
        return json.dumps(
            [
                {
                    "id": str(ob.id),
                    "revised_severity": "high",
                    "quality_confidence": 82,
                    "reasoning": "statutory",
                },
                {
                    "id": str(risk.id),
                    "revised_severity": "critical",
                    "quality_confidence": 90,
                    "reasoning": "penalty clause",
                },
            ]
        )

    fake_settings = types.SimpleNamespace(
        raw={"rescoring": {"enabled": True, "model": "test-model", "max_items_per_call": 50}}
    )

    monkeypatch.setattr(rescore_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(rescore_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(rescore_task, "settings", fake_settings)
    monkeypatch.setattr(rescore_task, "llm_completion", _fake_llm_completion)

    rescore_task.rescore_with_llm(str(doc.id))

    assert ob.llm_severity == Severity.high
    assert ob.llm_quality_confidence == 82
    assert risk.llm_severity == Severity.critical
    assert risk.llm_quality_confidence == 90
    assert ob.severity == Severity.medium
    assert risk.severity == Severity.medium


def test_rescore_skips_when_disabled(monkeypatch):
    doc = _make_document()
    ob = _make_obligation(doc.id)
    db = FakeSession(document=doc, obligations=[ob])

    fake_settings = types.SimpleNamespace(
        raw={"rescoring": {"enabled": False, "model": "test-model", "max_items_per_call": 50}}
    )

    monkeypatch.setattr(rescore_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(rescore_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(rescore_task, "settings", fake_settings)

    rescore_task.rescore_with_llm(str(doc.id))

    assert ob.llm_severity is None
    assert ob.llm_quality_confidence is None


def test_rescore_gracefully_handles_llm_failure(monkeypatch):
    doc = _make_document()
    ob = _make_obligation(doc.id, severity=Severity.medium)
    ob_ev = _make_evidence(doc.id, ob.id)
    db = FakeSession(document=doc, obligations=[ob], obligation_evidence=[ob_ev])

    def _failing_llm(model: str, prompt: str) -> str:
        raise RuntimeError("LLM unavailable")

    fake_settings = types.SimpleNamespace(
        raw={"rescoring": {"enabled": True, "model": "test-model", "max_items_per_call": 50}}
    )

    monkeypatch.setattr(rescore_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(rescore_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(rescore_task, "settings", fake_settings)
    monkeypatch.setattr(rescore_task, "llm_completion", _failing_llm)

    rescore_task.rescore_with_llm(str(doc.id))

    assert ob.llm_severity is None
    assert ob.severity == Severity.medium


def test_rescore_ignores_unknown_ids_in_llm_response(monkeypatch):
    doc = _make_document()
    ob = _make_obligation(doc.id, severity=Severity.medium)
    ob_ev = _make_evidence(doc.id, ob.id)
    db = FakeSession(document=doc, obligations=[ob], obligation_evidence=[ob_ev])

    def _fake_llm(model: str, prompt: str) -> str:
        return json.dumps(
            [
                {
                    "id": str(uuid.uuid4()),
                    "revised_severity": "critical",
                    "quality_confidence": 99,
                    "reasoning": "wrong",
                },
                {
                    "id": str(ob.id),
                    "revised_severity": "high",
                    "quality_confidence": 75,
                    "reasoning": "correct",
                },
            ]
        )

    fake_settings = types.SimpleNamespace(
        raw={"rescoring": {"enabled": True, "model": "test-model", "max_items_per_call": 50}}
    )

    monkeypatch.setattr(rescore_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(rescore_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(rescore_task, "settings", fake_settings)
    monkeypatch.setattr(rescore_task, "llm_completion", _fake_llm)

    rescore_task.rescore_with_llm(str(doc.id))

    assert ob.llm_severity == Severity.high
    assert ob.llm_quality_confidence == 75


def test_rescore_clamps_quality_confidence(monkeypatch):
    doc = _make_document()
    ob = _make_obligation(doc.id)
    ob_ev = _make_evidence(doc.id, ob.id)
    db = FakeSession(document=doc, obligations=[ob], obligation_evidence=[ob_ev])

    def _fake_llm(model: str, prompt: str) -> str:
        return json.dumps(
            [
                {
                    "id": str(ob.id),
                    "revised_severity": "low",
                    "quality_confidence": 150,
                    "reasoning": "over",
                }
            ]
        )

    fake_settings = types.SimpleNamespace(
        raw={"rescoring": {"enabled": True, "model": "test-model", "max_items_per_call": 50}}
    )

    monkeypatch.setattr(rescore_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(rescore_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(rescore_task, "settings", fake_settings)
    monkeypatch.setattr(rescore_task, "llm_completion", _fake_llm)

    rescore_task.rescore_with_llm(str(doc.id))

    assert ob.llm_quality_confidence == 100


def test_rescore_builds_prompt_with_item_context(monkeypatch):
    doc = _make_document()
    ob = _make_obligation(doc.id, obligation_text="Pay retainage")
    risk = _make_risk(doc.id, risk_text="Missed deadline penalty")
    ob_ev = _make_evidence(doc.id, ob.id)
    risk_ev = _make_evidence(doc.id, risk.id, is_risk=True)
    db = FakeSession(
        document=doc,
        obligations=[ob],
        risks=[risk],
        obligation_evidence=[ob_ev],
        risk_evidence=[risk_ev],
    )

    calls: list[tuple[str, str]] = []

    def _fake_llm(model: str, prompt: str) -> str:
        calls.append((model, prompt))
        return "[]"

    fake_settings = types.SimpleNamespace(
        raw={"rescoring": {"enabled": True, "model": "test-model", "max_items_per_call": 50}}
    )

    monkeypatch.setattr(rescore_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(rescore_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(rescore_task, "settings", fake_settings)
    monkeypatch.setattr(rescore_task, "llm_completion", _fake_llm)

    rescore_task.rescore_with_llm(str(doc.id))

    assert calls
    assert calls[0][0] == "test-model"
    assert "Document type: contract" in calls[0][1]
    assert str(ob.id) in calls[0][1]
    assert str(risk.id) in calls[0][1]
    assert "Pay retainage" in calls[0][1]
    assert "Missed deadline penalty" in calls[0][1]


def test_rescore_prompt_uses_financial_persona_for_deed_of_trust():
    document = _make_document(doc_type=DocumentType.deed_of_trust, domain="financial")
    prompt = rescore_task._build_rescore_prompt(document, obligations=[], risks=[])
    assert "financial compliance analyst" in prompt


def test_rescore_prompt_uses_real_estate_persona():
    document = _make_document(doc_type=DocumentType.purchase_agreement, domain="real_estate")
    prompt = rescore_task._build_rescore_prompt(document, obligations=[], risks=[])
    assert "real estate attorney" in prompt


def test_rescore_prompt_defaults_to_document_analyst_when_domain_none():
    document = _make_document(doc_type=DocumentType.unknown, domain=None)
    prompt = rescore_task._build_rescore_prompt(document, obligations=[], risks=[])
    assert "document analyst" in prompt
