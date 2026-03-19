from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import (
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
    TextSource,
)
from ._helpers import update_parse_status


_DEADLINE_RE = re.compile(r"\b(by|before|within|no later than|after|days?|weeks?|months?)\b", re.IGNORECASE)


def _clamp_score(score: int) -> int:
    return max(0, min(100, score))


def _score_config() -> tuple[dict[str, int], dict[str, int]]:
    defaults_weights = {
        "quote_verified": 40,
        "strong_modality": 15,
        "due_date_resolved": 10,
        "responsible_party_linked": 10,
        "doc_type_aligned": 10,
        "verifier_pass": 15,
    }
    defaults_penalties = {
        "weak_modality": -25,
        "ocr_source": -15,
        "contradiction": -30,
        "missing_deadline": -10,
    }

    scoring_cfg = settings.raw.get("scoring", {})
    weight_cfg = scoring_cfg.get("weights", {})
    penalty_cfg = scoring_cfg.get("penalties", {})

    weights = {k: int(weight_cfg.get(k, v)) for k, v in defaults_weights.items()}
    penalties = {k: int(penalty_cfg.get(k, v)) for k, v in defaults_penalties.items()}
    return weights, penalties


def _doc_type_aligned(doc_type: DocumentType, obligation_type: ObligationType) -> bool:
    if doc_type == DocumentType.invoice:
        return obligation_type == ObligationType.payment
    return True


def _implies_deadline(text: str) -> bool:
    return bool(_DEADLINE_RE.search(text or ""))


def _score_obligation(
    obligation: Obligation,
    document: Document,
    evidence: list[ObligationEvidence],
    weights: dict[str, int],
    penalties: dict[str, int],
) -> None:
    if obligation.status == ReviewStatus.rejected:
        return

    if not evidence:
        obligation.system_confidence = 0
        obligation.status = ReviewStatus.rejected
        return

    score = 0
    score += weights["quote_verified"]

    if obligation.modality in (Modality.must, Modality.shall, Modality.required):
        score += weights["strong_modality"]

    has_due_rule = bool((obligation.due_rule or "").strip())
    if obligation.due_kind in (DueKind.absolute, DueKind.resolved_relative) or has_due_rule:
        score += weights["due_date_resolved"]

    if obligation.responsible_entity_id is not None:
        score += weights["responsible_party_linked"]

    if _doc_type_aligned(document.doc_type, obligation.obligation_type):
        score += weights["doc_type_aligned"]

    score += weights["verifier_pass"]

    if obligation.modality in (Modality.should, Modality.may):
        score += penalties["weak_modality"]

    if any(ev.source == TextSource.ocr for ev in evidence):
        score += penalties["ocr_source"]

    if obligation.contradiction_flag:
        score += penalties["contradiction"]

    if _implies_deadline(obligation.obligation_text) and not (obligation.due_date or has_due_rule):
        score += penalties["missing_deadline"]

    obligation.system_confidence = _clamp_score(int(score))
    obligation.status = ReviewStatus.needs_review if obligation.system_confidence >= 50 else ReviewStatus.rejected


def _score_risk(
    risk: Risk,
    evidence: list[RiskEvidence],
    weights: dict[str, int],
    penalties: dict[str, int],
) -> None:
    if risk.status == ReviewStatus.rejected:
        return

    if not evidence:
        risk.system_confidence = 0
        risk.status = ReviewStatus.rejected
        return

    score = 0
    score += weights["quote_verified"]
    score += weights["verifier_pass"]

    if any(ev.source == TextSource.ocr for ev in evidence):
        score += penalties["ocr_source"]

    if risk.contradiction_flag:
        score += penalties["contradiction"]

    risk.system_confidence = _clamp_score(int(score))
    risk.status = ReviewStatus.needs_review if risk.system_confidence >= 50 else ReviewStatus.rejected


def score_extractions(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.scoring)

    db: Session = SessionLocal()
    try:
        doc_id = document_id if isinstance(document_id, uuid.UUID) else uuid.UUID(str(document_id))
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed:
            return

        obligations = db.query(Obligation).filter(Obligation.document_id == document.id).all()
        risks = db.query(Risk).filter(Risk.document_id == document.id).all()

        obligation_evidence = db.query(ObligationEvidence).filter(ObligationEvidence.document_id == document.id).all()
        risk_evidence = db.query(RiskEvidence).filter(RiskEvidence.document_id == document.id).all()

        evidence_by_obligation: dict[uuid.UUID, list[ObligationEvidence]] = {}
        for ev in obligation_evidence:
            evidence_by_obligation.setdefault(ev.obligation_id, []).append(ev)

        evidence_by_risk: dict[uuid.UUID, list[RiskEvidence]] = {}
        for ev in risk_evidence:
            evidence_by_risk.setdefault(ev.risk_id, []).append(ev)

        weights, penalties = _score_config()

        for obligation in obligations:
            _score_obligation(
                obligation=obligation,
                document=document,
                evidence=evidence_by_obligation.get(obligation.id, []),
                weights=weights,
                penalties=penalties,
            )
            db.add(obligation)

        for risk in risks:
            _score_risk(
                risk=risk,
                evidence=evidence_by_risk.get(risk.id, []),
                weights=weights,
                penalties=penalties,
            )
            db.add(risk)

        db.commit()
    finally:
        db.close()

