"""Stage 5b: classify each chunk as agreement_body or non_agreement.

Uses Haiku for fast, cheap classification. Chunks labeled `non_agreement`
are filtered out before extraction stages 6-10b, improving precision by
preventing the LLM from extracting obligations/risks from statutory
disclosures, tenant rights notices, and other non-agreement appendices.
"""
from __future__ import annotations

import logging
import uuid

from ...config import settings
from ...database import SessionLocal
from ...models import Chunk, Document, ParseStatus
from ._helpers import update_parse_status

logger = logging.getLogger(__name__)

SECTION_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"

_SECTION_CLASSIFY_PROMPT = (
    "You are a document structure analyst. For each numbered section below, "
    "classify it as one of:\n"
    '- "agreement_body" — part of the actual contract, lease, or agreement '
    "(clauses, terms, conditions, schedules, exhibits that impose duties or allocate risk)\n"
    '- "non_agreement" — statutory disclosures, tenant rights notices, '
    "informational appendices, government-mandated summaries, definitions of law, "
    "attached forms not part of the agreement, or boilerplate cover pages\n\n"
    "GUIDELINES:\n"
    "- Signature blocks, witness sections, and notary acknowledgments are agreement_body.\n"
    "- Sections titled 'Disclosure', 'Notice', 'Tenant Rights', 'Summary of Law', "
    "'Information About...', or similar are non_agreement.\n"
    "- Schedules and exhibits that contain specific terms (rent amounts, dates, "
    "property descriptions) are agreement_body.\n"
    "- When uncertain, classify as agreement_body (err on the side of inclusion).\n\n"
    "Return ONLY a JSON array of objects, one per section, in the same order:\n"
    '[{{"section": 1, "label": "agreement_body"}}, '
    '{{"section": 2, "label": "non_agreement"}}, ...]\n\n'
    "SECTIONS:\n{sections}"
)


def call_section_classify_llm(*, model: str, prompt: str) -> list[dict]:
    """Call LLM for section classification. Returns parsed list of dicts."""
    from ...services.llm import llm_completion, parse_json_list

    raw = llm_completion(model, prompt, prefer_json_object=False)
    return parse_json_list(raw)


def _build_section_classify_prompt(chunks: list[Chunk]) -> str:
    """Build prompt with numbered sections for classification."""
    sections = []
    for idx, chunk in enumerate(chunks, 1):
        page = chunk.page_number or "?"
        # Truncate to first 500 chars — enough for classification, saves tokens
        preview = (chunk.text or "")[:500]
        sections.append(f"--- Section {idx} (Page {page}) ---\n{preview}")
    return _SECTION_CLASSIFY_PROMPT.format(sections="\n\n".join(sections))


def classify_chunk_sections(document_id: str) -> dict[str, object]:
    """Classify each chunk as agreement_body or non_agreement."""
    update_parse_status(document_id, ParseStatus.extraction)

    db = SessionLocal()
    try:
        doc_id = uuid.UUID(document_id) if isinstance(document_id, str) else document_id
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": document_id, "status": "skipped", "reason": "parse_failed"}

        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc_id)
            .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
            .all()
        )

        if not chunks:
            return {"document_id": document_id, "status": "ok", "total": 0, "agreement_body": 0, "non_agreement": 0}

        model = settings.raw.get("section_classification", {}).get("model", SECTION_CLASSIFY_MODEL)
        prompt = _build_section_classify_prompt(chunks)

        try:
            labels = call_section_classify_llm(model=model, prompt=prompt)
        except Exception:
            logger.warning("Section classification failed; defaulting all chunks to agreement_body")
            for chunk in chunks:
                chunk.section_label = "agreement_body"
            db.commit()
            return {
                "document_id": document_id,
                "status": "fallback",
                "total": len(chunks),
                "agreement_body": len(chunks),
                "non_agreement": 0,
            }

        # Build label map from response
        label_map: dict[int, str] = {}
        for item in labels:
            if not isinstance(item, dict):
                continue
            section = item.get("section")
            label = item.get("label", "")
            if isinstance(section, int) and label in ("agreement_body", "non_agreement"):
                label_map[section] = label

        agreement_count = 0
        non_agreement_count = 0
        for idx, chunk in enumerate(chunks, 1):
            label = label_map.get(idx, "agreement_body")  # default to agreement_body
            chunk.section_label = label
            if label == "agreement_body":
                agreement_count += 1
            else:
                non_agreement_count += 1
        db.commit()

        logger.info(
            "Section classification for %s: %d agreement_body, %d non_agreement (of %d)",
            document_id[:8] if len(document_id) > 8 else document_id,
            agreement_count, non_agreement_count, len(chunks),
        )
        return {
            "document_id": document_id,
            "status": "ok",
            "model": model,
            "total": len(chunks),
            "agreement_body": agreement_count,
            "non_agreement": non_agreement_count,
        }
    finally:
        db.close()
