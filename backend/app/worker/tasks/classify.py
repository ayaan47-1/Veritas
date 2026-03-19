from __future__ import annotations

from datetime import datetime, timezone
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


def _heuristics_match(doc_type: DocumentType, text_blob: str) -> bool:
    text = text_blob.lower()

    if doc_type == DocumentType.invoice:
        has_currency = bool(re.search(r"\$\s?\d", text))
        return has_currency or any(token in text for token in ["usd", "amount", "total", "invoice"])

    if doc_type == DocumentType.inspection_report:
        return any(token in text for token in ["inspect", "examin", "assess", "finding"])

    if doc_type == DocumentType.contract:
        return any(token in text for token in ["agree", "party", "parties", "shall", "obligation"])

    if doc_type == DocumentType.rfi:
        return any(token in text for token in ["request for information", "clarification", "rfi"])

    if doc_type == DocumentType.change_order:
        return any(token in text for token in ["change order", "modification", "amendment"])

    if doc_type == DocumentType.unknown:
        return True

    return False


def _build_prompt(sample_pages: list[str]) -> str:
    joined = "\n\n".join(sample_pages)[:12000]
    return (
        "Classify the document type as one of: contract, inspection_report, rfi, "
        "change_order, invoice, unknown. Return compact JSON: "
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


def classify_document(document_id: str) -> None:
    update_parse_status(document_id, ParseStatus.classification)

    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed:
            return

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
            document.doc_type_confidence = None
            run.status = ExtractionStatus.failed
            run.error = str(error)[:1000] if error else "classification_failed"
            run.raw_llm_output = {"error": run.error}
            if model_used:
                run.model_used = model_used
            db.add(document)
            db.add(run)
            db.commit()
            return

        detected_type = _coerce_doc_type(response.get("doc_type"))
        confidence_raw = response.get("confidence")
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = None

        joined_text = " ".join(page_texts)
        heuristics_agree = _heuristics_match(detected_type, joined_text)

        if detected_type != DocumentType.unknown and heuristics_agree:
            document.doc_type = detected_type
            document.doc_type_confidence = confidence
        elif detected_type == DocumentType.unknown:
            document.doc_type = DocumentType.unknown
            document.doc_type_confidence = confidence
        else:
            document.doc_type = DocumentType.unknown
            document.doc_type_confidence = None

        run.status = ExtractionStatus.completed
        run.model_used = model_used or run.model_used
        run.raw_llm_output = response

        db.add(document)
        db.add(run)
        db.commit()
    finally:
        db.close()

