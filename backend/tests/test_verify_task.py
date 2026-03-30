from __future__ import annotations

import hashlib
import sys
import types
import uuid



from backend.app.models import (
    Document,
    DocumentPage,
    DueKind,
    Modality,
    Obligation,
    ObligationContradiction,
    ObligationEvidence,
    ObligationType,
    PageProcessingStatus,
    ParseStatus,
    ReviewStatus,
    Risk,
    RiskEvidence,
    RiskType,
    Severity,
    TextSource,
)
from backend.app.worker.tasks import verify as verify_task


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

    def delete(self, synchronize_session: str | bool = False) -> int:
        matched = self.all()
        storage = self._storage_for_model()
        if storage is not None:
            for row in matched:
                if row in storage:
                    storage.remove(row)
        return len(matched)

    def in_(self, sub):
        """Stub for subquery .in_() — returns a truthy placeholder."""
        return True

    def _storage_for_model(self):
        if self._model is verify_task.ObligationEvidence:
            return self._session.obligation_evidence
        if self._model is verify_task.RiskEvidence:
            return self._session.risk_evidence
        if self._model is verify_task.ObligationContradiction:
            return self._session.contradictions
        if self._model is verify_task.Obligation:
            return self._session.obligations
        if self._model is verify_task.Risk:
            return self._session.risks
        return None

    def _rows_for_model(self):
        if self._model is verify_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is verify_task.DocumentPage:
            return list(self._session.pages)
        if self._model is verify_task.Obligation:
            return list(self._session.obligations)
        if self._model is verify_task.Risk:
            return list(self._session.risks)
        if self._model is verify_task.ObligationEvidence:
            return list(self._session.obligation_evidence)
        if self._model is verify_task.RiskEvidence:
            return list(self._session.risk_evidence)
        if self._model is verify_task.ObligationContradiction:
            return list(self._session.contradictions)
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
        pages: list[DocumentPage],
        obligations: list[Obligation] | None = None,
        risks: list[Risk] | None = None,
    ):
        self.document = document
        self.pages = pages
        self.obligations = obligations or []
        self.risks = risks or []
        self.obligation_evidence: list[ObligationEvidence] = []
        self.risk_evidence: list[RiskEvidence] = []
        self.contradictions: list[ObligationContradiction] = []

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
        if isinstance(obj, ObligationEvidence):
            if obj not in self.obligation_evidence:
                self.obligation_evidence.append(obj)
            return
        if isinstance(obj, RiskEvidence):
            if obj not in self.risk_evidence:
                self.risk_evidence.append(obj)
            return
        if isinstance(obj, ObligationContradiction):
            if obj not in self.contradictions:
                self.contradictions.append(obj)
            return

    def delete(self, obj):
        if isinstance(obj, Risk) and obj in self.risks:
            self.risks.remove(obj)
        elif isinstance(obj, ObligationContradiction) and obj in self.contradictions:
            self.contradictions.remove(obj)

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
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.verification,
        scanned_page_count=0,
    )


def _make_page(document_id: uuid.UUID, text: str, source: TextSource = TextSource.pdf_text) -> DocumentPage:
    return DocumentPage(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=1,
        raw_text=text,
        normalized_text=text,
        text_source=source,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        processing_status=PageProcessingStatus.processed,
    )


def _make_obligation(document_id: uuid.UUID, text: str, due_date=None, severity: Severity = Severity.medium) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        document_id=document_id,
        obligation_type=ObligationType.payment,
        obligation_text=text,
        modality=Modality.shall,
        due_kind=DueKind.absolute if due_date else DueKind.none,
        due_date=due_date,
        due_rule=None,
        trigger_date=None,
        severity=severity,
        status=ReviewStatus.needs_review,
        system_confidence=0,
        has_external_reference=False,
        contradiction_flag=False,
    )


def _make_risk(document_id: uuid.UUID, text: str) -> Risk:
    return Risk(
        id=uuid.uuid4(),
        document_id=document_id,
        risk_type=RiskType.schedule,
        risk_text=text,
        severity=Severity.high,
        status=ReviewStatus.needs_review,
        system_confidence=0,
        has_external_reference=False,
        contradiction_flag=False,
    )


def test_verify_creates_evidence_for_matching_obligation_and_risk(monkeypatch):
    document = _make_document()
    quote = "Contractor shall pay $1,000 by 2026-06-15"
    page = _make_page(document.id, f"Intro. {quote}. End.")
    ob = _make_obligation(document.id, quote)
    risk = _make_risk(document.id, quote)

    db = FakeSession(document=document, pages=[page], obligations=[ob], risks=[risk])

    monkeypatch.setattr(verify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(verify_task, "update_parse_status", lambda *_a, **_k: None)

    verify_task.verify_extractions(document.id)

    assert ob.status == ReviewStatus.needs_review
    assert risk.status == ReviewStatus.needs_review
    assert len(db.obligation_evidence) == 1
    assert len(db.risk_evidence) == 1

    ob_ev = db.obligation_evidence[0]
    assert ob_ev.page_number == 1
    assert ob_ev.source == TextSource.pdf_text
    assert ob_ev.normalized_char_start >= 0
    assert ob_ev.normalized_char_end > ob_ev.normalized_char_start


def test_verify_rejects_when_quote_not_found(monkeypatch):
    document = _make_document()
    page = _make_page(document.id, "No matching quote is present here.")
    ob = _make_obligation(document.id, "This exact quote does not exist")

    db = FakeSession(document=document, pages=[page], obligations=[ob], risks=[])

    monkeypatch.setattr(verify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(verify_task, "update_parse_status", lambda *_a, **_k: None)

    verify_task.verify_extractions(document.id)

    assert ob.status == ReviewStatus.rejected
    assert len(db.obligation_evidence) == 0


def test_verify_detects_contradictions_and_creates_conflict_risk(monkeypatch):
    from datetime import date

    document = _make_document()
    q1 = "Contractor shall pay $1,000 by 2026-06-15"
    q2 = "Contractor shall pay $2,000 by 2026-06-20"
    page = _make_page(document.id, f"{q1}. Also {q2}.")

    ob1 = _make_obligation(document.id, q1, due_date=date(2026, 6, 15), severity=Severity.medium)
    ob2 = _make_obligation(document.id, q2, due_date=date(2026, 6, 20), severity=Severity.high)

    db = FakeSession(document=document, pages=[page], obligations=[ob1, ob2], risks=[])

    monkeypatch.setattr(verify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(verify_task, "update_parse_status", lambda *_a, **_k: None)

    verify_task.verify_extractions(document.id)

    assert ob1.contradiction_flag is True
    assert ob2.contradiction_flag is True

    conflict_risks = [r for r in db.risks if r.risk_type == RiskType.contractual]
    assert len(conflict_risks) == 1
    assert len(db.contradictions) == 1


def test_verify_skips_duplicate_risk_evidence_when_contradiction_reuses_existing_quote(monkeypatch):
    from datetime import date

    document = _make_document()
    q1 = "Contractor shall pay $1,000 by 2026-06-15"
    q2 = "Contractor shall pay $2,000 by 2026-06-20"
    page = _make_page(document.id, f"{q1}. Also {q2}.")

    ob1 = _make_obligation(document.id, q1, due_date=date(2026, 6, 15), severity=Severity.medium)
    ob2 = _make_obligation(document.id, q2, due_date=date(2026, 6, 20), severity=Severity.high)
    risk = _make_risk(document.id, q1)

    db = FakeSession(document=document, pages=[page], obligations=[ob1, ob2], risks=[risk])

    monkeypatch.setattr(verify_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(verify_task, "update_parse_status", lambda *_a, **_k: None)

    verify_task.verify_extractions(document.id)

    conflict_risks = [r for r in db.risks if r.risk_type == RiskType.contractual]
    assert len(conflict_risks) == 1
    assert len(db.contradictions) == 1
    assert len(db.risk_evidence) == 2
