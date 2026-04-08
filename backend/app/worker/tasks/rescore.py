from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import (
    Document,
    Obligation,
    ObligationEvidence,
    ParseStatus,
    ReviewStatus,
    Risk,
    RiskEvidence,
    Severity,
)
from ...services.llm import llm_completion, parse_json_list
from ._helpers import update_parse_status

logger = logging.getLogger(__name__)


_RESCORE_PROMPT_TEMPLATE = (
    "You are a {persona}. Re-evaluate extraction quality and severity.\n\n"
    "Document type: {doc_type}\n\n"
    "Items to evaluate:\n"
    "{items_block}\n\n"
    "Return strict JSON array only. Each item must include:\n"
    '  "id": string UUID,\n'
    '  "revised_severity": one of low|medium|high|critical,\n'
    '  "quality_confidence": integer 0-100,\n'
    '  "reasoning": short rationale.\n'
)

_DOMAIN_PERSONAS = {
    "construction": "construction contract analyst",
    "real_estate": "real estate attorney",
    "financial": "financial compliance analyst",
    "general": "document analyst",
}


def _coerce_severity(raw: object) -> Severity | None:
    if not isinstance(raw, str):
        return None
    try:
        return Severity(raw.strip().lower())
    except ValueError:
        return None


def _clamp(value: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, value))


def _build_items_block(items: list[Obligation | Risk], evidence_pages: dict[uuid.UUID, list[int]]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, Obligation):
            kind = "obligation"
            item_type = item.obligation_type.value
            quote = item.obligation_text
        else:
            kind = "risk"
            item_type = item.risk_type.value
            quote = item.risk_text

        pages = sorted(set(evidence_pages.get(item.id, [])))
        page_text = ", ".join(str(page) for page in pages) if pages else "none"
        lines.append(
            f"{idx}. [{kind}] id={item.id} type={item_type} severity={item.severity.value} "
            f"confidence={item.system_confidence}\n"
            f'   Quote: "{(quote or "")[:300]}"\n'
            f"   Evidence pages: {page_text}"
        )
    return "\n".join(lines)


def _build_rescore_prompt(
    document: Document,
    obligations: list[Obligation],
    risks: list[Risk],
    evidence_pages: dict[uuid.UUID, list[int]] | None = None,
) -> str:
    domain = document.domain or "general"
    persona = _DOMAIN_PERSONAS.get(domain, "document analyst")
    pages = evidence_pages or {}
    items: list[Obligation | Risk] = [*obligations, *risks]
    return _RESCORE_PROMPT_TEMPLATE.format(
        persona=persona,
        doc_type=document.doc_type.value,
        items_block=_build_items_block(items, pages),
    )


def rescore_with_llm(document_id: str) -> dict[str, object]:
    rescoring_cfg = settings.raw.get("rescoring", {})
    if not rescoring_cfg.get("enabled", False):
        return {"document_id": document_id, "status": "skipped", "reason": "disabled"}

    update_parse_status(document_id, ParseStatus.rescoring)

    db: Session = SessionLocal()
    try:
        doc_id = document_id if isinstance(document_id, uuid.UUID) else uuid.UUID(str(document_id))
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document or document.parse_status == ParseStatus.failed:
            reason = "not_found" if not document else "parse_failed"
            return {"document_id": str(document_id), "status": "skipped", "reason": reason}

        obligations = db.query(Obligation).filter(Obligation.document_id == doc_id).all()
        risks = db.query(Risk).filter(Risk.document_id == doc_id).all()
        if not obligations and not risks:
            return {"document_id": str(document.id), "status": "skipped", "reason": "no_items"}

        obligation_evidence = db.query(ObligationEvidence).filter(ObligationEvidence.document_id == doc_id).all()
        risk_evidence = db.query(RiskEvidence).filter(RiskEvidence.document_id == doc_id).all()

        pages_by_item: dict[uuid.UUID, list[int]] = {}
        for row in obligation_evidence:
            pages_by_item.setdefault(row.obligation_id, []).append(row.page_number)
        for row in risk_evidence:
            pages_by_item.setdefault(row.risk_id, []).append(row.page_number)

        all_items: list[Obligation | Risk] = [*obligations, *risks]
        items_by_id = {str(item.id): item for item in all_items}

        model = str(rescoring_cfg.get("model", "claude-haiku-4-5-20251001"))
        max_items = int(rescoring_cfg.get("max_items_per_call", 50) or 50)
        max_items = max(1, max_items)
        updated_item_count = 0

        for start in range(0, len(all_items), max_items):
            batch = all_items[start : start + max_items]
            batch_obligations = [item for item in batch if isinstance(item, Obligation)]
            batch_risks = [item for item in batch if isinstance(item, Risk)]
            prompt = _build_rescore_prompt(
                document=document,
                obligations=batch_obligations,
                risks=batch_risks,
                evidence_pages=pages_by_item,
            )
            try:
                raw = llm_completion(model, prompt)
                results = parse_json_list(raw)
            except Exception:
                logger.exception("Rescore LLM call failed for document %s", document_id)
                return {
                    "document_id": str(document.id),
                    "status": "failed",
                    "model_used": model,
                    "item_count": len(all_items),
                    "updated_item_count": updated_item_count,
                }

            for entry in results:
                item = items_by_id.get(str(entry.get("id", "")))
                if item is None:
                    continue

                revised_severity = _coerce_severity(entry.get("revised_severity"))
                if revised_severity is not None:
                    item.llm_severity = revised_severity

                raw_conf = entry.get("quality_confidence")
                if isinstance(raw_conf, (int, float)):
                    item.llm_quality_confidence = _clamp(int(raw_conf))
                    if item.llm_quality_confidence < 30 and item.status == ReviewStatus.needs_review:
                        item.status = ReviewStatus.rejected
                        logger.info(
                            "LLM confidence %d < 30 for item %s — downgrading to rejected",
                            item.llm_quality_confidence, item.id,
                        )

                db.add(item)
                updated_item_count += 1

        db.commit()
        return {
            "document_id": str(document.id),
            "status": "ok",
            "model_used": model,
            "item_count": len(all_items),
            "updated_item_count": updated_item_count,
        }
    except Exception:
        logger.exception("Rescore stage failed for document %s", document_id)
        return {"document_id": str(document_id), "status": "failed"}
    finally:
        db.close()
