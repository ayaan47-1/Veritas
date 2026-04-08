from __future__ import annotations

import hashlib
import sys
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
from backend.app.worker.tasks import score as score_task


SCORING_DOMAINS = {
    "financial": {
        "doc_types": ["insurance_policy", "loan_agreement", "deed_of_trust"],
        "doc_type_aligned": {
            "insurance_policy": ["payment", "compliance", "notification"],
            "loan_agreement": ["payment", "compliance", "submission"],
            "deed_of_trust": ["payment", "compliance"],
        },
    },
    "general": {
        "doc_types": ["unknown"],
        "doc_type_aligned": {},
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
        "obligation_text": "Contractor shall pay $1,000 by 2026-06-15",
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
        "risk_type": RiskType.schedule,
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


def test_doc_type_aligned_insurance_policy_payment_true(monkeypatch):
    monkeypatch.setattr(score_task, "settings", types.SimpleNamespace(raw={"domains": SCORING_DOMAINS}))
    assert score_task._doc_type_aligned(DocumentType.insurance_policy, ObligationType.payment)


def test_doc_type_aligned_deed_of_trust_submission_false(monkeypatch):
    monkeypatch.setattr(score_task, "settings", types.SimpleNamespace(raw={"domains": SCORING_DOMAINS}))
    assert not score_task._doc_type_aligned(DocumentType.deed_of_trust, ObligationType.submission)


def test_doc_type_aligned_unknown_always_true(monkeypatch):
    monkeypatch.setattr(score_task, "settings", types.SimpleNamespace(raw={"domains": SCORING_DOMAINS}))
    assert score_task._doc_type_aligned(DocumentType.unknown, ObligationType.inspection)


def test_score_insurance_policy_payment_obligation_awards_alignment_bonus(monkeypatch):
    payment_document = _make_document(DocumentType.insurance_policy)
    payment_obligation = _make_obligation(payment_document.id, obligation_type=ObligationType.payment)
    payment_evidence = _make_obligation_evidence(payment_document.id, payment_obligation.id, TextSource.pdf_text)
    payment_db = FakeSession(
        document=payment_document,
        obligations=[payment_obligation],
        obligation_evidence=[payment_evidence],
    )

    monkeypatch.setattr(score_task, "settings", types.SimpleNamespace(raw={"domains": SCORING_DOMAINS, "scoring": {}}))
    monkeypatch.setattr(score_task, "SessionLocal", lambda: payment_db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)
    score_task.score_extractions(payment_document.id)
    payment_score = payment_obligation.system_confidence

    submission_document = _make_document(DocumentType.insurance_policy)
    submission_obligation = _make_obligation(submission_document.id, obligation_type=ObligationType.submission)
    submission_evidence = _make_obligation_evidence(submission_document.id, submission_obligation.id, TextSource.pdf_text)
    submission_db = FakeSession(
        document=submission_document,
        obligations=[submission_obligation],
        obligation_evidence=[submission_evidence],
    )

    monkeypatch.setattr(score_task, "SessionLocal", lambda: submission_db)
    score_task.score_extractions(submission_document.id)
    submission_score = submission_obligation.system_confidence

    assert payment_score > submission_score, "Aligned obligation type should score higher"
    assert payment_score >= 50, "Aligned payment obligation should not be rejected"
    assert submission_score >= 50, "Unaligned obligation should still pass if evidence exists"


def test_score_obligation_full_positive_score(monkeypatch):
    document = _make_document(DocumentType.invoice)
    obligation = _make_obligation(document.id, due_date=None, due_rule="within 10 days")
    evidence = _make_obligation_evidence(document.id, obligation.id, TextSource.pdf_text)
    db = FakeSession(document=document, obligations=[obligation], obligation_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert obligation.system_confidence >= 75, "Full positive signals should score high"
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

    assert obligation.system_confidence < 50, "Heavy penalties should push score below rejection threshold"
    assert obligation.status == ReviewStatus.rejected


def test_score_risk_applies_penalties_and_gating(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(document.id, contradiction_flag=True)
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.ocr)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence < 50, "OCR + contradiction penalties should reject"
    assert risk.status == ReviewStatus.rejected


def test_score_risk_statute_reference_adds_points(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(
        document.id,
        risk_text="Withhold amounts pursuant to C.R.S. § 38-26-107",
    )
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.pdf_text)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence >= 50, "Statute reference should keep risk above rejection"


def test_score_risk_monetary_amount_adds_points(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(
        document.id,
        risk_text="Penalty of $50,000 for non-compliance",
    )
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.pdf_text)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence >= 50, "Signal should keep risk above rejection threshold"


def test_score_risk_deadline_adds_points(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(
        document.id,
        risk_text="Must be resolved within 30 days of notice",
    )
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.pdf_text)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence >= 50, "Signal should keep risk above rejection threshold"


def test_score_risk_external_reference_adds_points(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(
        document.id,
        has_external_reference=True,
    )
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.pdf_text)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence >= 50, "Signal should keep risk above rejection threshold"


def test_score_risk_contradiction_flag_adds_cross_obligation_points(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(
        document.id,
        contradiction_flag=True,
    )
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.pdf_text)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence < 50, "Contradiction penalty should reject despite cross-obligation bonus"
    assert risk.status == ReviewStatus.rejected


def test_score_risk_combined_signals(monkeypatch):
    document = _make_document(DocumentType.contract)
    risk = _make_risk(
        document.id,
        risk_text="Penalty of $10,000 pursuant to § 12.3 within 60 days",
        has_external_reference=True,
    )
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.pdf_text)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence >= 70, "Combined positive signals should score high"


def test_score_obligation_statute_reference_adds_points(monkeypatch):
    document = _make_document(DocumentType.contract)
    obligation = _make_obligation(
        document.id,
        obligation_type=ObligationType.compliance,
        obligation_text="Comply with C.R.S. § 38-26-107 requirements",
        due_kind=DueKind.none,
        due_date=None,
        due_rule=None,
    )
    evidence = _make_obligation_evidence(document.id, obligation.id, TextSource.pdf_text)
    db = FakeSession(document=document, obligations=[obligation], obligation_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert obligation.system_confidence >= 75, "Statute reference should score high"


def test_score_proportional_fuzzy_penalty_low_similarity_penalizes_more(monkeypatch):
    """A fuzzy match at 0.85 should be penalized more than one at 0.99."""
    document = _make_document(DocumentType.contract)

    ob_low = _make_obligation(document.id)
    ev_low = _make_obligation_evidence(document.id, ob_low.id, TextSource.pdf_text)
    ev_low.verification_method = "fuzzy"
    ev_low.fuzzy_similarity = 0.86

    ob_high = _make_obligation(document.id)
    ev_high = _make_obligation_evidence(document.id, ob_high.id, TextSource.pdf_text)
    ev_high.verification_method = "fuzzy"
    ev_high.fuzzy_similarity = 0.98

    db = FakeSession(
        document=document,
        obligations=[ob_low, ob_high],
        obligation_evidence=[ev_low, ev_high],
    )
    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert ob_high.system_confidence > ob_low.system_confidence, (
        "Higher fuzzy similarity should produce a higher score"
    )


def test_score_payment_obligation_without_amount_penalized(monkeypatch):
    """A payment obligation that doesn't mention a dollar amount gets penalized."""
    document = _make_document(DocumentType.contract)
    ob_with = _make_obligation(document.id, obligation_text="Pay $5,000 by June")
    ev_with = _make_obligation_evidence(document.id, ob_with.id, TextSource.pdf_text)

    ob_without = _make_obligation(document.id, obligation_text="Pay the contractor by June")
    ev_without = _make_obligation_evidence(document.id, ob_without.id, TextSource.pdf_text)

    db = FakeSession(
        document=document,
        obligations=[ob_with, ob_without],
        obligation_evidence=[ev_with, ev_without],
    )
    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert ob_with.system_confidence > ob_without.system_confidence, (
        "Payment obligation with dollar amount should score higher"
    )


def test_score_sentence_verified_gets_penalty(monkeypatch):
    """Sentence-verified evidence should get a penalty similar to fuzzy."""
    document = _make_document(DocumentType.contract)
    ob_exact = _make_obligation(document.id)
    ev_exact = _make_obligation_evidence(document.id, ob_exact.id, TextSource.pdf_text)
    ev_exact.verification_method = "exact"

    ob_sentence = _make_obligation(document.id)
    ev_sentence = _make_obligation_evidence(document.id, ob_sentence.id, TextSource.pdf_text)
    ev_sentence.verification_method = "sentence"

    db = FakeSession(
        document=document,
        obligations=[ob_exact, ob_sentence],
        obligation_evidence=[ev_exact, ev_sentence],
    )
    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert ob_exact.system_confidence > ob_sentence.system_confidence, (
        "Exact match should score higher than sentence-split match"
    )


# --- Golden regression tests (exact scores pinned for canonical scenarios) ---


def test_golden_score_strong_obligation_exact_evidence(monkeypatch):
    """Pin: strong obligation (shall, linked entity, due rule, aligned type) with exact PDF evidence."""
    document = _make_document(DocumentType.invoice)
    obligation = _make_obligation(document.id, due_date=None, due_rule="within 10 days")
    evidence = _make_obligation_evidence(document.id, obligation.id, TextSource.pdf_text)
    db = FakeSession(document=document, obligations=[obligation], obligation_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert obligation.system_confidence == 100


def test_golden_score_risk_with_ocr_and_contradiction(monkeypatch):
    """Pin: risk with OCR source + contradiction flag should be rejected."""
    document = _make_document(DocumentType.contract)
    risk = _make_risk(document.id, contradiction_flag=True)
    evidence = _make_risk_evidence(document.id, risk.id, TextSource.ocr)
    db = FakeSession(document=document, risks=[risk], risk_evidence=[evidence])

    monkeypatch.setattr(score_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(score_task, "update_parse_status", lambda *_a, **_k: None)

    score_task.score_extractions(document.id)

    assert risk.system_confidence == 15
    assert risk.status == ReviewStatus.rejected
