from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import logging
import re
import uuid

from sqlalchemy.orm import Session

from ...config import settings
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


logger = logging.getLogger(__name__)

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
        page_text = normalize_text(page.normalized_text or "")
        idx = page_text.find(normalized_quote)
        if idx >= 0:
            end = idx + len(normalized_quote)
            return page, normalized_quote, idx, end
    return None


def _fuzzy_find_quote_in_pages(
    quote: str, pages: list[DocumentPage], threshold: float
) -> tuple[DocumentPage, str, int, int, float] | None:
    """Fuzzy fallback: sliding-window SequenceMatcher across pages.

    Returns (page, matched_page_text, start, end, similarity) or None.
    """
    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        return None

    quote_len = len(normalized_quote)
    # Allow windows 20% shorter to 20% longer than the quote.
    min_window = max(1, int(quote_len * 0.8))
    max_window = int(quote_len * 1.2)

    best: tuple[DocumentPage, str, int, int, float] | None = None
    best_ratio = 0.0

    for page in pages:
        page_text = normalize_text(page.normalized_text or "")
        if not page_text:
            continue
        # Quick check: skip pages with very low overall similarity.
        if SequenceMatcher(None, normalized_quote, page_text).quick_ratio() < 0.3:
            continue

        for win_size in range(min_window, max_window + 1, max(1, (max_window - min_window) // 10)):
            for start in range(0, len(page_text) - win_size + 1, max(1, win_size // 4)):
                candidate = page_text[start : start + win_size]
                ratio = SequenceMatcher(None, normalized_quote, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = (page, candidate, start, start + win_size, ratio)
                    if ratio >= 0.98:
                        # Close enough — stop early.
                        break
            if best_ratio >= 0.98:
                break
        if best_ratio >= 0.98:
            break

    if best is not None and best_ratio >= threshold:
        return best
    return None


_SECTION_MARKER_RE = re.compile(
    r"(?:REMEDY|PROVIDED THAT|EXCEPTION|NOTE|WARNING|CONDITION|PROVISO)\s*:",
    re.IGNORECASE,
)

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

_MIN_SENTENCE_LEN = 30


def _sentence_find_quote_in_pages(
    quote: str, pages: list[DocumentPage],
) -> tuple[DocumentPage, str, int, int] | None:
    """Split a multi-sentence quote and try to match individual sentences."""
    # Split on legal section markers first, then on sentence boundaries
    parts = _SECTION_MARKER_RE.split(quote)
    sentences: list[str] = []
    for part in parts:
        sentences.extend(_SENTENCE_BOUNDARY_RE.split(part))

    # Filter short fragments and sort longest first
    candidates = [s.strip() for s in sentences if len(s.strip()) >= _MIN_SENTENCE_LEN]
    candidates.sort(key=len, reverse=True)

    for sentence in candidates:
        result = _find_quote_in_pages(sentence, pages)
        if result is not None:
            return result
    return None


def _verification_config() -> tuple[float, int]:
    cfg = settings.raw.get("verification", {})
    threshold = float(cfg.get("fuzzy_threshold", 0.85))
    penalty = int(cfg.get("fuzzy_penalty", -10))
    return threshold, penalty


def _verify_obligations(
    db: Session,
    document: Document,
    pages: list[DocumentPage],
    obligations: list[Obligation],
) -> tuple[dict[uuid.UUID, list[ObligationEvidence]], dict[str, int]]:
    seen_keys: set[tuple[str, uuid.UUID, int, int, int]] = set()
    evidence_by_obligation: dict[uuid.UUID, list[ObligationEvidence]] = {}
    fuzzy_threshold, _ = _verification_config()

    stats = {"total": 0, "exact": 0, "sentence": 0, "fuzzy": 0, "rejected_quote_mismatch": 0, "deduped": 0, "external_ref": 0}

    for obligation in obligations:
        stats["total"] += 1
        quote = obligation.obligation_text or ""
        obligation.has_external_reference = _has_external_reference(quote)
        if obligation.has_external_reference:
            stats["external_ref"] += 1

        located = _find_quote_in_pages(quote, pages)
        verification_method = "exact"
        fuzzy_similarity: float | None = None

        if not located:
            sentence_result = _sentence_find_quote_in_pages(quote, pages)
            if sentence_result:
                located = sentence_result
                verification_method = "sentence"

        if not located:
            fuzzy_result = _fuzzy_find_quote_in_pages(quote, pages, fuzzy_threshold)
            if not fuzzy_result:
                stats["rejected_quote_mismatch"] += 1
                logger.info(
                    "VERIFY REJECT obligation %s — quote not found in any page (first 120 chars: %s)",
                    obligation.id, quote[:120],
                )
                obligation.status = ReviewStatus.rejected
                db.add(obligation)
                continue
            page, matched_text, start, end, similarity = fuzzy_result
            normalized_quote = matched_text
            verification_method = "fuzzy"
            fuzzy_similarity = similarity
            logger.info(
                "Fuzzy-verified obligation %s (similarity=%.3f) on page %s",
                obligation.id, similarity, page.page_number,
            )
        else:
            page, normalized_quote, start, end = located

        dedup_key = (_sha256(normalized_quote), document.id, page.page_number, start, end)
        if dedup_key in seen_keys:
            stats["deduped"] += 1
            logger.info(
                "VERIFY DEDUP obligation %s — evidence already exists for same span on page %s",
                obligation.id, page.page_number,
            )
            continue
        seen_keys.add(dedup_key)

        stats[verification_method] += 1

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
            verification_method=verification_method,
            fuzzy_similarity=fuzzy_similarity,
        )
        db.add(evidence)
        db.add(obligation)
        evidence_by_obligation.setdefault(obligation.id, []).append(evidence)

    db.commit()
    logger.info(
        "VERIFY OBLIGATIONS SUMMARY — total=%d exact=%d sentence=%d fuzzy=%d rejected_quote_mismatch=%d deduped=%d external_ref=%d",
        stats["total"], stats["exact"], stats["sentence"], stats["fuzzy"],
        stats["rejected_quote_mismatch"], stats["deduped"], stats["external_ref"],
    )
    return evidence_by_obligation, stats


def _verify_risks(db: Session, document: Document, pages: list[DocumentPage], risks: list[Risk]) -> tuple[dict[uuid.UUID, list[RiskEvidence]], dict[str, int]]:
    seen_keys: set[tuple[str, uuid.UUID, int, int, int]] = set()
    evidence_by_risk: dict[uuid.UUID, list[RiskEvidence]] = {}
    fuzzy_threshold, _ = _verification_config()

    stats = {"total": 0, "exact": 0, "sentence": 0, "fuzzy": 0, "rejected_quote_mismatch": 0, "deduped": 0, "external_ref": 0}

    for risk in risks:
        stats["total"] += 1
        quote = risk.risk_text or ""
        risk.has_external_reference = _has_external_reference(quote)
        if risk.has_external_reference:
            stats["external_ref"] += 1

        located = _find_quote_in_pages(quote, pages)
        verification_method = "exact"
        fuzzy_similarity: float | None = None

        if not located:
            sentence_result = _sentence_find_quote_in_pages(quote, pages)
            if sentence_result:
                located = sentence_result
                verification_method = "sentence"

        if not located:
            fuzzy_result = _fuzzy_find_quote_in_pages(quote, pages, fuzzy_threshold)
            if not fuzzy_result:
                stats["rejected_quote_mismatch"] += 1
                logger.info(
                    "VERIFY REJECT risk %s — quote not found in any page (first 120 chars: %s)",
                    risk.id, quote[:120],
                )
                risk.status = ReviewStatus.rejected
                db.add(risk)
                continue
            page, matched_text, start, end, similarity = fuzzy_result
            normalized_quote = matched_text
            verification_method = "fuzzy"
            fuzzy_similarity = similarity
            logger.info(
                "Fuzzy-verified risk %s (similarity=%.3f) on page %s",
                risk.id, similarity, page.page_number,
            )
        else:
            page, normalized_quote, start, end = located

        dedup_key = (_sha256(normalized_quote), document.id, page.page_number, start, end)
        if dedup_key in seen_keys:
            stats["deduped"] += 1
            logger.info(
                "VERIFY DEDUP risk %s — evidence already exists for same span on page %s",
                risk.id, page.page_number,
            )
            continue
        seen_keys.add(dedup_key)

        stats[verification_method] += 1

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
            verification_method=verification_method,
            fuzzy_similarity=fuzzy_similarity,
        )
        db.add(evidence)
        db.add(risk)
        evidence_by_risk.setdefault(risk.id, []).append(evidence)

    db.commit()
    logger.info(
        "VERIFY RISKS SUMMARY — total=%d exact=%d sentence=%d fuzzy=%d rejected_quote_mismatch=%d deduped=%d external_ref=%d",
        stats["total"], stats["exact"], stats["sentence"], stats["fuzzy"],
        stats["rejected_quote_mismatch"], stats["deduped"], stats["external_ref"],
    )
    return evidence_by_risk, stats


def _payment_amounts(text: str) -> set[str]:
    return {m.group(1) for m in _AMOUNT_RE.finditer(text or "")}


def _risk_evidence_key(
    document_id: uuid.UUID,
    quote_sha256: str,
    page_number: int,
    normalized_char_start: int,
    normalized_char_end: int,
) -> tuple[str, uuid.UUID, int, int, int]:
    return (quote_sha256, document_id, page_number, normalized_char_start, normalized_char_end)


def _detect_contradictions(
    db: Session,
    document: Document,
    obligations: list[Obligation],
    evidence_by_obligation: dict[uuid.UUID, list[ObligationEvidence]],
) -> None:
    pair_seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    existing_risk_evidence_keys = {
        _risk_evidence_key(
            ev.document_id,
            ev.quote_sha256,
            ev.page_number,
            ev.normalized_char_start,
            ev.normalized_char_end,
        )
        for ev in db.query(RiskEvidence).filter(RiskEvidence.document_id == document.id).all()
    }

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
            db.flush()  # ensure risk row exists before junction FK references it

            junction = ObligationContradiction(
                id=uuid.uuid4(),
                obligation_a_id=first.id,
                obligation_b_id=second.id,
                risk_id=risk.id,
                detected_at=datetime.now(timezone.utc),
            )
            db.add(junction)

            # Best-effort linkage evidence for contradiction risk from existing obligation evidence.
            seen_ev_keys: set[tuple] = set()
            for ob in (a, b):
                for ev in evidence_by_obligation.get(ob.id, []):
                    ev_key = _risk_evidence_key(
                        document.id,
                        ev.quote_sha256,
                        ev.page_number,
                        ev.normalized_char_start,
                        ev.normalized_char_end,
                    )
                    if ev_key in seen_ev_keys:
                        continue
                    if ev_key in existing_risk_evidence_keys:
                        logger.info(
                            "Skipping duplicate contradiction risk evidence for document_id=%s risk_id=%s page=%s span=%s-%s quote_sha256=%s",
                            document.id,
                            risk.id,
                            ev.page_number,
                            ev.normalized_char_start,
                            ev.normalized_char_end,
                            ev.quote_sha256,
                        )
                        continue
                    seen_ev_keys.add(ev_key)
                    existing_risk_evidence_keys.add(ev_key)
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


def verify_extractions(document_id: str) -> dict[str, object]:
    update_parse_status(document_id, ParseStatus.verification)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": str(document.id), "status": "skipped", "reason": "parse_failed"}

        # Idempotency: clear evidence from any previous partial run so retries
        # don't hit UniqueViolation on the (quote_sha256, doc, page, offsets) constraint.
        db.query(ObligationEvidence).filter(ObligationEvidence.document_id == document.id).delete()
        db.query(RiskEvidence).filter(RiskEvidence.document_id == document.id).delete()
        # Reset flags that verification sets so they're recomputed cleanly.
        obligations_pre = db.query(Obligation).filter(Obligation.document_id == document.id).all()
        for ob in obligations_pre:
            ob.status = ReviewStatus.needs_review
            ob.has_external_reference = False
            ob.contradiction_flag = False
            db.add(ob)
        risks_pre = db.query(Risk).filter(Risk.document_id == document.id).all()
        # Remove contradiction-generated risks (they'll be re-detected).
        for rk in risks_pre:
            if rk.contradiction_flag and rk.risk_type == RiskType.contractual:
                db.delete(rk)
            else:
                rk.status = ReviewStatus.needs_review
                rk.has_external_reference = False
                rk.contradiction_flag = False
                db.add(rk)
        # Clear contradiction junction records for this document's obligations.
        ob_ids = {ob.id for ob in obligations_pre}
        for ctr in db.query(ObligationContradiction).all():
            if ctr.obligation_a_id in ob_ids or ctr.obligation_b_id in ob_ids:
                db.delete(ctr)
        db.commit()

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

        evidence_by_obligation, ob_verify_stats = _verify_obligations(db, document, pages, obligations)
        evidence_by_risk, ri_verify_stats = _verify_risks(db, document, pages, risks)
        pre_contradiction_risk_count = db.query(Risk).filter(Risk.document_id == document.id).count()
        _detect_contradictions(db, document, obligations, evidence_by_obligation)
        post_contradiction_risk_count = db.query(Risk).filter(Risk.document_id == document.id).count()
        return {
            "document_id": str(document.id),
            "status": "ok",
            "obligation_count": len(obligations),
            "risk_count": len(risks),
            "obligation_evidence_count": sum(len(items) for items in evidence_by_obligation.values()),
            "risk_evidence_count": sum(len(items) for items in evidence_by_risk.values()),
            "rejected_obligation_count": sum(1 for obligation in obligations if obligation.status == ReviewStatus.rejected),
            "rejected_risk_count": sum(1 for risk in risks if risk.status == ReviewStatus.rejected),
            "contradiction_risk_count": post_contradiction_risk_count - pre_contradiction_risk_count,
            "obligation_verify_breakdown": ob_verify_stats,
            "risk_verify_breakdown": ri_verify_stats,
        }
    finally:
        db.close()
