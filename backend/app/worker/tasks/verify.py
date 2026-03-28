from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import uuid

from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models import (
    Document,
    DocumentPage,
    Obligation,
    ObligationContradiction,
    ObligationEvidence,
    ParseStatus,
    ReviewStatus,
    Risk,
    RiskEvidence,
    RiskType,
    Severity,
)
from ...services.normalization import normalize_text
from ._helpers import update_parse_status


_EXTERNAL_REF_PATTERNS = [
    "per exhibit",
    "as defined in",
    "pursuant to",
    "referenced in",
    "attached hereto",
]

_AMOUNT_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _has_external_reference(text: str) -> bool:
    lower = text.lower()
    return any(pattern in lower for pattern in _EXTERNAL_REF_PATTERNS)


def _find_quote_in_pages(quote: str, pages: list[DocumentPage]):
    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        return None

    for page in pages:
        page_text = page.normalized_text or ""
        idx = page_text.find(normalized_quote)
        if idx >= 0:
            end = idx + len(normalized_quote)
            return page, normalized_quote, idx, end
    return None


def _verify_obligations(
    db: Session,
    document: Document,
    pages: list[DocumentPage],
    obligations: list[Obligation],
) -> dict[uuid.UUID, list[ObligationEvidence]]:
    seen_keys: set[tuple[str, uuid.UUID, int, int, int]] = set()
    evidence_by_obligation: dict[uuid.UUID, list[ObligationEvidence]] = {}

    for obligation in obligations:
        quote = obligation.obligation_text or ""
        obligation.has_external_reference = _has_external_reference(quote)

        located = _find_quote_in_pages(quote, pages)
        if not located:
            obligation.status = ReviewStatus.rejected
            db.add(obligation)
            continue

        page, normalized_quote, start, end = located
        dedup_key = (_sha256(normalized_quote), document.id, page.page_number, start, end)
        if dedup_key in seen_keys:
            # Duplicate suppression within verification run.
            continue
        seen_keys.add(dedup_key)

        evidence = ObligationEvidence(
            id=uuid.uuid4(),
            obligation_id=obligation.id,
            document_id=document.id,
            page_number=page.page_number,
            quote=normalized_quote,
            quote_sha256=_sha256(normalized_quote),
            raw_char_start=start,
            raw_char_end=end,
            normalized_char_start=start,
            normalized_char_end=end,
            bbox_x1=None,
            bbox_y1=None,
            bbox_x2=None,
            bbox_y2=None,
            source=page.text_source,
        )
        db.add(evidence)
        db.add(obligation)
        evidence_by_obligation.setdefault(obligation.id, []).append(evidence)

    db.commit()
    return evidence_by_obligation


def _verify_risks(db: Session, document: Document, pages: list[DocumentPage], risks: list[Risk]) -> dict[uuid.UUID, list[RiskEvidence]]:
    seen_keys: set[tuple[str, uuid.UUID, int, int, int]] = set()
    evidence_by_risk: dict[uuid.UUID, list[RiskEvidence]] = {}

    for risk in risks:
        quote = risk.risk_text or ""
        risk.has_external_reference = _has_external_reference(quote)

        located = _find_quote_in_pages(quote, pages)
        if not located:
            risk.status = ReviewStatus.rejected
            db.add(risk)
            continue

        page, normalized_quote, start, end = located
        dedup_key = (_sha256(normalized_quote), document.id, page.page_number, start, end)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        evidence = RiskEvidence(
            id=uuid.uuid4(),
            risk_id=risk.id,
            document_id=document.id,
            page_number=page.page_number,
            quote=normalized_quote,
            quote_sha256=_sha256(normalized_quote),
            raw_char_start=start,
            raw_char_end=end,
            normalized_char_start=start,
            normalized_char_end=end,
            bbox_x1=None,
            bbox_y1=None,
            bbox_x2=None,
            bbox_y2=None,
            source=page.text_source,
        )
        db.add(evidence)
        db.add(risk)
        evidence_by_risk.setdefault(risk.id, []).append(evidence)

    db.commit()
    return evidence_by_risk


def _payment_amounts(text: str) -> set[str]:
    return {m.group(1) for m in _AMOUNT_RE.finditer(text or "")}


def _detect_contradictions(
    db: Session,
    document: Document,
    obligations: list[Obligation],
    evidence_by_obligation: dict[uuid.UUID, list[ObligationEvidence]],
) -> None:
    pair_seen: set[tuple[uuid.UUID, uuid.UUID]] = set()

    for i in range(len(obligations)):
        a = obligations[i]
        if a.status == ReviewStatus.rejected:
            continue
        for j in range(i + 1, len(obligations)):
            b = obligations[j]
            if b.status == ReviewStatus.rejected:
                continue

            conflict = False

            if a.obligation_type == b.obligation_type and a.due_date and b.due_date and a.due_date != b.due_date:
                conflict = True

            if (
                a.obligation_type == b.obligation_type
                and a.responsible_entity_id is not None
                and a.responsible_entity_id == b.responsible_entity_id
                and a.severity != b.severity
            ):
                conflict = True

            if a.obligation_type.value == "payment" and b.obligation_type.value == "payment":
                amounts_a = _payment_amounts(a.obligation_text)
                amounts_b = _payment_amounts(b.obligation_text)
                if amounts_a and amounts_b and amounts_a != amounts_b:
                    conflict = True

            if not conflict:
                continue

            a.contradiction_flag = True
            b.contradiction_flag = True
            db.add(a)
            db.add(b)

            first, second = (a, b) if str(a.id) < str(b.id) else (b, a)
            pair = (first.id, second.id)
            if pair in pair_seen:
                continue
            pair_seen.add(pair)

            risk = Risk(
                id=uuid.uuid4(),
                document_id=document.id,
                risk_type=RiskType.contractual,
                risk_text="Potential contradiction detected between obligations",
                severity=Severity.high,
                status=ReviewStatus.needs_review,
                system_confidence=0,
                reviewer_confidence=None,
                has_external_reference=False,
                contradiction_flag=True,
                extraction_run_id=None,
            )
            db.add(risk)

            junction = ObligationContradiction(
                id=uuid.uuid4(),
                obligation_a_id=first.id,
                obligation_b_id=second.id,
                risk_id=risk.id,
                detected_at=datetime.now(timezone.utc),
            )
            db.add(junction)

            # Best-effort linkage evidence for contradiction risk from existing obligation evidence.
            for ob in (a, b):
                for ev in evidence_by_obligation.get(ob.id, []):
                    db.add(
                        RiskEvidence(
                            id=uuid.uuid4(),
                            risk_id=risk.id,
                            document_id=document.id,
                            page_number=ev.page_number,
                            quote=ev.quote,
                            quote_sha256=ev.quote_sha256,
                            raw_char_start=ev.raw_char_start,
                            raw_char_end=ev.raw_char_end,
                            normalized_char_start=ev.normalized_char_start,
                            normalized_char_end=ev.normalized_char_end,
                            bbox_x1=ev.bbox_x1,
                            bbox_y1=ev.bbox_y1,
                            bbox_x2=ev.bbox_x2,
                            bbox_y2=ev.bbox_y2,
                            source=ev.source,
                        )
                    )

    db.commit()


def verify_extractions(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.verification)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed:
            return

        pages = (
            db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )

        obligations = (
            db.query(Obligation)
            .filter(Obligation.document_id == document.id)
            .order_by(Obligation.created_at.asc())
            .all()
        )
        risks = (
            db.query(Risk)
            .filter(Risk.document_id == document.id)
            .order_by(Risk.created_at.asc())
            .all()
        )

        evidence_by_obligation = _verify_obligations(db, document, pages, obligations)
        _verify_risks(db, document, pages, risks)
        _detect_contradictions(db, document, obligations, evidence_by_obligation)
    finally:
        db.close()
