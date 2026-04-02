from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import time
import uuid

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import (
    Document,
    DocumentPage,
    DocumentType,
    ExtractionRun,
    ExtractionStage,
    ExtractionStatus,
    ParseStatus,
    PromptVersion,
)
from ._helpers import update_parse_status

logger = logging.getLogger(__name__)

_WARNED_MISSING_DOMAINS = False


def call_classification_llm(*, model: str, prompt: str) -> dict:
    """Call LLM for classification via LiteLLM."""
    from ...services.llm import classify as _llm_classify

    return _llm_classify(model=model, prompt=prompt)


def _coerce_doc_type(value: object) -> DocumentType:
    if not isinstance(value, str):
        return DocumentType.unknown
    normalized = value.strip().lower()
    try:
        return DocumentType(normalized)
    except ValueError:
        return DocumentType.unknown


def _extract_sample_pages(db: Session, document_id: uuid.UUID, limit: int) -> list[str]:
    pages = (
        db.query(DocumentPage)
        .filter(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number.asc())
        .all()
    )
    sample = []
    for page in pages:
        text = (page.normalized_text or page.raw_text or "").strip()
        if text:
            sample.append(text)
        if len(sample) >= limit:
            break
    return sample


def _domains_config() -> dict[str, dict]:
    global _WARNED_MISSING_DOMAINS
    domains = settings.raw.get("domains", {})
    if isinstance(domains, dict):
        if not domains and not _WARNED_MISSING_DOMAINS:
            logger.warning("Missing 'domains' config; classification is using fallback defaults")
            _WARNED_MISSING_DOMAINS = True
        return domains

    if not _WARNED_MISSING_DOMAINS:
        logger.warning("Invalid 'domains' config; classification is using fallback defaults")
        _WARNED_MISSING_DOMAINS = True
    return {}


def _domain_for_doc_type(doc_type: DocumentType) -> str:
    for domain_name, domain_data in _domains_config().items():
        if doc_type.value in domain_data.get("doc_types", []):
            return domain_name
    return "general"


def _heuristics_match(doc_type: DocumentType, text_blob: str) -> bool:
    text = text_blob.lower()
    if doc_type == DocumentType.unknown:
        return True

    # Keep invoice currency as a regex special case; it is not a fixed keyword token.
    if doc_type == DocumentType.invoice:
        if re.search(r"\$\s?\d", text):
            return True

    for domain_data in _domains_config().values():
        tokens = domain_data.get("heuristics", {}).get(doc_type.value, [])
        if tokens and any(token in text for token in tokens):
            return True

    return False


def _build_prompt(sample_pages: list[str]) -> str:
    all_types: list[str] = []
    for domain_data in _domains_config().values():
        all_types.extend(domain_data.get("doc_types", []))
    if not all_types:
        all_types = [doc_type.value for doc_type in DocumentType]
    type_list = ", ".join(sorted(set(all_types)))
    joined = "\n\n".join(sample_pages)[:12000]
    return (
        f"Classify the document type as one of: {type_list}. Return compact JSON: "
        '{"doc_type":"...","confidence":0.0,"explanation":"..."}.\n\n'
        f"Document excerpts:\n{joined}"
    )


def _get_or_create_prompt_version(db: Session, uploaded_by: uuid.UUID) -> PromptVersion:
    prompt = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.prompt_name == "classification_doc_type",
            PromptVersion.is_active == True,  # noqa: E712
        )
        .order_by(PromptVersion.version.desc())
        .first()
    )
    if prompt:
        return prompt

    existing = (
        db.query(PromptVersion)
        .filter(PromptVersion.prompt_name == "classification_doc_type")
        .all()
    )
    next_version = max([p.version for p in existing], default=0) + 1

    prompt = PromptVersion(
        id=uuid.uuid4(),
        prompt_name="classification_doc_type",
        version=next_version,
        template="Classify document type from first pages.",
        doc_type=None,
        description="Auto-generated default prompt for classification stage",
        is_active=True,
        created_by=uploaded_by,
    )
    db.add(prompt)
    db.commit()
    return prompt


def _run_with_retries(prompt: str, llm_cfg: dict) -> tuple[str | None, dict | None, Exception | None]:
    models = [llm_cfg.get("primary_model", "gpt-4o")] + list(llm_cfg.get("fallback_models", []))
    max_retries = max(1, int(llm_cfg.get("max_retries", 3)))
    backoff_base = max(1, int(llm_cfg.get("retry_backoff_base", 2)))

    last_error: Exception | None = None

    for model in models:
        for attempt in range(max_retries):
            try:
                return model, call_classification_llm(model=model, prompt=prompt), None
            except Exception as exc:  # pragma: no cover - failure path covered via tests with monkeypatch
                last_error = exc
                if attempt < max_retries - 1:
                    delay_seconds = backoff_base ** (attempt + 1)
                    time.sleep(delay_seconds)

    return None, None, last_error


def classify_document(document_id: str) -> dict[str, object]:
    update_parse_status(document_id, ParseStatus.classification)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": str(document.id), "status": "skipped", "reason": "parse_failed"}

        sample_pages = int(settings.raw.get("classification", {}).get("sample_pages", 3))
        page_texts = _extract_sample_pages(db, document.id, sample_pages)
        prompt = _build_prompt(page_texts)

        llm_cfg = settings.raw.get("llm", {})
        prompt_version = _get_or_create_prompt_version(db, document.uploaded_by)

        run = ExtractionRun(
            id=uuid.uuid4(),
            document_id=document.id,
            prompt_version_id=prompt_version.id,
            model_used=str(llm_cfg.get("primary_model", "gpt-4o")),
            config_snapshot={
                "llm": llm_cfg,
                "classification": {"sample_pages": sample_pages},
            },
            stage=ExtractionStage.classification,
            status=ExtractionStatus.running,
        )
        db.add(run)
        db.commit()

        model_used, response, error = _run_with_retries(prompt, llm_cfg)

        run.completed_at = datetime.now(timezone.utc)

        if response is None:
            document.doc_type = DocumentType.unknown
            document.domain = _domain_for_doc_type(DocumentType.unknown)
            document.doc_type_confidence = None
            run.status = ExtractionStatus.failed
            run.error = str(error)[:1000] if error else "classification_failed"
            run.raw_llm_output = {"error": run.error}
            if model_used:
                run.model_used = model_used
            db.add(document)
            db.add(run)
            db.commit()
            return {
                "document_id": str(document.id),
                "status": "failed",
                "run_id": str(run.id),
                "doc_type": document.doc_type.value,
                "doc_type_confidence": document.doc_type_confidence,
                "model_used": run.model_used,
                "sample_page_count": len(page_texts),
                "error": run.error,
            }

        detected_type = _coerce_doc_type(response.get("doc_type"))
        confidence_raw = response.get("confidence")
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = None

        joined_text = " ".join(page_texts)
        heuristics_agree = _heuristics_match(detected_type, joined_text)

        if detected_type != DocumentType.unknown and heuristics_agree:
            final_doc_type = detected_type
            final_confidence = confidence
        elif detected_type == DocumentType.unknown:
            final_doc_type = DocumentType.unknown
            final_confidence = confidence
        else:
            final_doc_type = DocumentType.unknown
            final_confidence = None

        document.doc_type = final_doc_type
        document.doc_type_confidence = final_confidence
        document.domain = _domain_for_doc_type(final_doc_type)

        run.status = ExtractionStatus.completed
        run.model_used = model_used or run.model_used
        run.raw_llm_output = response

        db.add(document)
        db.add(run)
        db.commit()
        return {
            "document_id": str(document.id),
            "status": "ok",
            "run_id": str(run.id),
            "doc_type": document.doc_type.value,
            "doc_type_confidence": document.doc_type_confidence,
            "model_used": run.model_used,
            "sample_page_count": len(page_texts),
        }
    finally:
        db.close()
