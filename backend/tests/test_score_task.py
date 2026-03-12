from __future__ import annotations

import hashlib
import sys
import types
import uuid


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
from backend.app.worker.tasks import score as score_task


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
        if self._model is score_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is score_task.Obligation:
            return list(self._session.obligations)
        if self._model is score_task.Risk:
            return list(self._session.risks)
        if self._model is score_task.ObligationEvidence:
            return list(self._session.obligation_evidence)
        if self._model is score_task.RiskEvidence:
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
        if isinstance(obj, Document):
            self.document = obj
            return
        if isinstance(obj, Obligation):
            if obj not in self.obligations:
                self.obligations.append(obj)
            return
        if isinstance(obj, Risk):
            if obj not in self.risks:
                self.risks.append(obj)
            return

    def commit(self):
        return None

    def rollback(self):
        return None

    def flush(self):
        return None

    def close(self):
        return None


def _make_document(doc_type: DocumentType) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.scoring,
        doc_type=doc_type,
        scanned_page_count=0,
    )


def _make_obligation(document_id: uuid.UUID, **overrides) -> Obligation:
    data = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "obligation_type": ObligationType.payment,
        "obligation_text": "Contractor shall pay by 2026-06-15",
        "modality": Modality.shall,
        "responsible_entity_id": uuid.uuid4(),
        "due_kind": DueKind.absolute,
        "due_date": None,
        "due_rule": None,
        "trigger_date": None,
        "severity": Severity.high,
        "status": ReviewStatus.needs_review,
        "system_confidence": 0,
        "reviewer_confidence": None,
        "has_external_reference": False,
        "contradiction_flag": False,
        "extraction_run_id": None,
    }
    data.update(overrides)
    return Obligation(**data)


def _make_obligation_evidence(document_id: uuid.UUID, obligation_id: uuid.UUID, source: TextSource) -> ObligationEvidence:
    return ObligationEvidence(
        id=uuid.uuid4(),
        obligation_id=obligation_id,
        document_id=document_id,
        page_number=1,
        quote="quoted text",
        quote_sha256=hashlib.sha256(b"quoted text").hexdigest(),
        raw_char_start=0,
        raw_char_end=11,
        normalized_char_start=0,
        normalized_char_end=11,
        source=source,
    )


def _make_risk(document_id: uuid.UUID, **overrides) -> Risk:
    data = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "risk_type": RiskType.scope_change_indicator,
        "risk_text": "Potential scope change",
        "severity": Severity.high,
        "status": ReviewStatus.needs_review,
        "system_confidence": 0,
        "reviewer_confidence": None,
        "has_external_reference": False,
        "contradiction_flag": False,
        "extraction_run_id": None,
    }
    data.update(overrides)
    return Risk(**data)


def _make_risk_evidence(document_id: uuid.UUID, risk_id: uuid.UUID, source: TextSource) -> RiskEvidence:
    return RiskEvidence(
        id=uuid.uuid4(),
        risk_id=risk_id,
        document_id=document_id,
        page_number=1,
        quote="risk quote",
        quote_sha256=hashlib.sha256(b"risk quote").hexdigest(),
        raw_char_start=0,
        raw_char_end=10,
        normalized_char_start=0,
        normalized_char_end=10,
        source=source,
    )


def test_score_obligation_full_positive_score(monkeypatch):
    document = _make_document(DocumentType.invoice)
    obligation = _make_obligation(document.id, due_date=None, due_rule="within 10 days")
    evidence = _make_obligation_evidence(document.id, obligation.id, TextSource.pdf_text)
    db = FakeSession(document=document, obligations=[obligation], obligation_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert obligation.system_confidence == 100
    assert obligation.status == ReviewStatus.needs_review


def test_score_obligation_penalties_can_reject(monkeypatch):
    document = _make_document(DocumentType.invoice)
    obligation = _make_obligation(
        document.id,
        obligation_type=ObligationType.inspection,
        obligation_text="Contractor should submit by next week",
        modality=Modality.should,
        responsible_entity_id=None,
        due_kind=DueKind.none,
        due_date=None,
        due_rule=None,
        contradiction_flag=True,
    )
    evidence = _make_obligation_evidence(document.id, obligation.id, TextSource.ocr)
    db = FakeSession(document=document, obligations=[obligation], obligation_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert obligation.system_confidence == 0
    assert obligation.status == ReviewStatus.rejected


def test_score_risk_applies_penalties_and_gating(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(document.id, contradiction_flag=True)
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.ocr)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence == 10
    assert risk.status == ReviewStatus.rejected

