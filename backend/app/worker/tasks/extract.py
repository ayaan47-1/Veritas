from __future__ import annotations

from datetime import date, datetime, timezone
from difflib import SequenceMatcher
import logging
import re
import time
import uuid

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import (
    Chunk,
    Document,
    DocumentType,
    DueKind,
    Entity,
    EntityMention,
    EntityType,
    ExtractionRun,
    ExtractionStage,
    ExtractionStatus,
    Modality,
    Obligation,
    ObligationType,
    ParseStatus,
    PromptVersion,
    ReviewStatus,
    Risk,
    RiskType,
    Severity,
)
from ._helpers import update_parse_status

logger = logging.getLogger(__name__)

_WARNED_MISSING_DOMAINS = False


def call_extract_llm(*, model: str, prompt: str, stage: str) -> list[dict]:
    """Call LLM for extraction via LiteLLM."""
    from ...services.llm import extract as _llm_extract

    return _llm_extract(model=model, prompt=prompt, stage=stage)


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _coerce_enum(value: object, enum_cls, default):
    if not isinstance(value, str):
        return default
    try:
        return enum_cls(value.strip().lower())
    except Exception:
        return default


def _domains_config() -> dict[str, dict]:
    global _WARNED_MISSING_DOMAINS
    domains = settings.raw.get("domains", {})
    if isinstance(domains, dict):
        if not domains and not _WARNED_MISSING_DOMAINS:
            logger.warning("Missing 'domains' config; extraction is using fallback defaults")
            _WARNED_MISSING_DOMAINS = True
        return domains

    if not _WARNED_MISSING_DOMAINS:
        logger.warning("Invalid 'domains' config; extraction is using fallback defaults")
        _WARNED_MISSING_DOMAINS = True
    return {}


def _domain_for_doc_type(doc_type: DocumentType) -> str:
    for domain_name, domain_data in _domains_config().items():
        if doc_type.value in domain_data.get("doc_types", []):
            return domain_name
    return "general"


def _get_stage_keywords(stage_name: str, doc_type: DocumentType) -> tuple[str, ...]:
    domain_data = _domains_config().get(_domain_for_doc_type(doc_type), {})
    keywords = domain_data.get("stage_keywords", {}).get(stage_name, [])
    if keywords:
        return tuple(str(item) for item in keywords)

    general = _domains_config().get("general", {})
    return tuple(str(item) for item in general.get("stage_keywords", {}).get(stage_name, ()))


def _get_obligation_aliases(doc_type: DocumentType) -> dict[str, str]:
    domain_data = _domains_config().get(_domain_for_doc_type(doc_type), {})
    aliases = domain_data.get("obligation_aliases", {})
    if isinstance(aliases, dict):
        return {str(key): str(value) for key, value in aliases.items()}
    return {}


def _get_vocab_preamble(stage_name: str, doc_type: DocumentType) -> str:
    domain_data = _domains_config().get(_domain_for_doc_type(doc_type), {})
    preamble = domain_data.get("vocab_preambles", {}).get(stage_name, "")
    return str(preamble or "")


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _suggest_entity_id(name: str, entities: list[Entity]) -> uuid.UUID | None:
    best_score = 0.0
    best_id: uuid.UUID | None = None

    for entity in entities:
        candidates = [entity.canonical_name] + list(entity.aliases or [])
        for candidate in candidates:
            score = _similarity(name, str(candidate))
            if score > best_score:
                best_score = score
                best_id = entity.id

    return best_id if best_score >= 0.85 else None


def _resolve_party_entity_id(name: str | None, entities: list[Entity]) -> uuid.UUID | None:
    if not name:
        return None
    return _suggest_entity_id(name, entities)


def _parse_due_fields(due_date_raw: object, due_rule_raw: object) -> tuple[DueKind, date | None, str | None]:
    due_rule = str(due_rule_raw).strip() if isinstance(due_rule_raw, str) and due_rule_raw.strip() else None

    due_date: date | None = None
    if isinstance(due_date_raw, str) and due_date_raw.strip():
        try:
            due_date = date.fromisoformat(due_date_raw.strip())
        except ValueError:
            due_date = None

    if due_date is not None:
        return DueKind.absolute, due_date, due_rule
    if due_rule:
        return DueKind.relative, None, due_rule
    return DueKind.none, None, None


def _get_or_create_prompt_version(db: Session, prompt_name: str, uploaded_by: uuid.UUID) -> PromptVersion:
    prompt = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.prompt_name == prompt_name,
            PromptVersion.is_active == True,  # noqa: E712
        )
        .order_by(PromptVersion.version.desc())
        .first()
    )
    if prompt:
        return prompt

    existing = db.query(PromptVersion).filter(PromptVersion.prompt_name == prompt_name).all()
    next_version = max([p.version for p in existing], default=0) + 1

    prompt = PromptVersion(
        id=uuid.uuid4(),
        prompt_name=prompt_name,
        version=next_version,
        template=f"Auto-generated default prompt for {prompt_name}",
        doc_type=None,
        description=f"Default {prompt_name}",
        is_active=True,
        created_by=uploaded_by,
    )
    db.add(prompt)
    db.commit()
    return prompt


_OBLIGATION_SCHEMA = (
    'Extract every obligation (duty, requirement, or commitment) from the chunk. '
    'For each obligation return a JSON object with these exact fields:\n'
    '  "quote": verbatim sentence(s) from the text that state the obligation (required),\n'
    '  "obligation_type": one of payment|delivery|reporting|compliance|maintenance|notification|other,\n'
    '  "modality": one of must|shall|will|should|may|unknown,\n'
    '  "severity": one of low|medium|high|critical,\n'
    '  "due_date": ISO date string or null,\n'
    '  "due_rule": relative deadline description or null,\n'
    '  "responsible_party": name of the obligor or null.\n'
    'Return [] if no obligations found. Return strict JSON array only.'
)

_RISK_SCHEMA = (
    'Extract every risk, liability, or penalty clause from the chunk. '
    'For each risk return a JSON object with these exact fields:\n'
    '  "quote": verbatim sentence(s) from the text describing the risk (required),\n'
    '  "risk_type": one of financial|schedule|quality|safety|compliance|contractual|unknown_risk,\n'
    '  "severity": one of low|medium|high|critical.\n'
    'Return [] if no risks found. Return strict JSON array only.'
)

_ENTITY_SCHEMA = (
    'Extract every named entity (person, company, organization, location) from the chunk. '
    'For each entity return a JSON object with these exact fields:\n'
    '  "entity_type": one of person|organization|location|agreement_date|other,\n'
    '  "entity_value": the exact name or value as it appears in the text,\n'
    '  "location": brief description of where in the text (e.g. "Section 1").\n'
    'Return [] if no entities found. Return strict JSON array only.'
)

_STAGE_SCHEMAS = {
    "obligation_extraction": _OBLIGATION_SCHEMA,
    "risk_extraction": _RISK_SCHEMA,
    "entity_extraction": _ENTITY_SCHEMA,
}


def _build_extraction_prompt(stage_name: str, chunk: Chunk, document: Document) -> str:
    schema = _STAGE_SCHEMAS.get(stage_name, "Return strict JSON array only.")
    vocab_preamble = _get_vocab_preamble(stage_name, document.doc_type)
    preamble = f"{vocab_preamble}\n\n" if vocab_preamble else ""
    return (
        f"Document type: {document.doc_type.value}\n"
        f"Page: {chunk.page_number}\n\n"
        f"{preamble}"
        f"{schema}\n\n"
        f"Chunk text:\n{chunk.text}"
    )


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


def _relevance_score(stage_name: str, text: str, doc_type: DocumentType) -> float:
    tokens = _token_set(text)
    if not tokens:
        return 0.0
    keywords = _get_stage_keywords(stage_name, doc_type)
    if not keywords:
        return min(1.0, len(tokens) / 200.0)
    text_lower = text.lower()
    hit_count = sum(1 for keyword in keywords if keyword.lower() in text_lower)
    keyword_score = hit_count / max(1, len(keywords))
    richness = min(1.0, len(tokens) / 200.0)
    return (0.75 * keyword_score) + (0.25 * richness)


def _select_chunks_for_stage(chunks: list[Chunk], stage_name: str, llm_cfg: dict, doc_type: DocumentType) -> list[Chunk]:
    selection_cfg = llm_cfg.get("chunk_selection", {}) if isinstance(llm_cfg, dict) else {}
    max_chunks = int(selection_cfg.get("max_chunks_per_stage", 0) or 0)
    if max_chunks <= 0 or max_chunks >= len(chunks):
        return chunks

    use_mmr = bool(selection_cfg.get("use_mmr", True))
    lambda_mult = float(selection_cfg.get("mmr_lambda", 0.7))
    lambda_mult = max(0.0, min(1.0, lambda_mult))

    scored = [
        {
            "chunk": chunk,
            "tokens": _token_set(chunk.text or ""),
            "relevance": _relevance_score(stage_name, chunk.text or "", doc_type),
        }
        for chunk in chunks
    ]
    scored.sort(key=lambda item: item["relevance"], reverse=True)
    if not use_mmr:
        return [item["chunk"] for item in scored[:max_chunks]]

    selected: list[dict] = []
    remaining = scored.copy()
    while remaining and len(selected) < max_chunks:
        if not selected:
            selected.append(remaining.pop(0))
            continue

        best_idx = 0
        best_score = -1e9
        for idx, candidate in enumerate(remaining):
            max_similarity = max(_jaccard(candidate["tokens"], chosen["tokens"]) for chosen in selected)
            mmr_score = (lambda_mult * candidate["relevance"]) - ((1.0 - lambda_mult) * max_similarity)
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx
        selected.append(remaining.pop(best_idx))

    selected_ids = {item["chunk"].id for item in selected}
    return [chunk for chunk in chunks if chunk.id in selected_ids]


def _run_chunk_calls(
    *,
    chunks: list[Chunk],
    stage_name: str,
    doc_type: DocumentType,
    llm_cfg: dict,
    build_prompt,
):
    models = [llm_cfg.get("primary_model", "gpt-4o")] + list(llm_cfg.get("fallback_models", []))
    max_retries = max(1, int(llm_cfg.get("max_retries", 3)))
    backoff_base = max(1, int(llm_cfg.get("retry_backoff_base", 2)))

    errors: list[dict] = []
    outputs: list[dict] = []
    active_model_idx = 0
    active_model = models[0] if models else "gpt-4o"

    ordered_chunks = _select_chunks_for_stage(chunks, stage_name, llm_cfg, doc_type)
    for chunk in ordered_chunks:
        chunk_done = False
        last_error: Exception | None = None

        while active_model_idx < len(models) and not chunk_done:
            model = models[active_model_idx]
            active_model = model
            prompt = build_prompt(model, chunk)

            for attempt in range(max_retries):
                try:
                    response = call_extract_llm(model=model, prompt=prompt, stage=stage_name)
                    outputs.append({"chunk_id": str(chunk.id), "model": model, "response": response})
                    chunk_done = True
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        time.sleep(backoff_base ** (attempt + 1))

            if not chunk_done:
                active_model_idx += 1

        if not chunk_done:
            errors.append(
                {
                    "chunk_id": str(chunk.id),
                    "page_number": chunk.page_number,
                    "error": str(last_error) if last_error else "unknown_error",
                }
            )

    return active_model, outputs, errors


def _start_run(
    *,
    db: Session,
    document: Document,
    stage: ExtractionStage,
    prompt_name: str,
    llm_cfg: dict,
) -> ExtractionRun:
    prompt_version = _get_or_create_prompt_version(db, prompt_name, document.uploaded_by)
    run = ExtractionRun(
        id=uuid.uuid4(),
        document_id=document.id,
        prompt_version_id=prompt_version.id,
        model_used=str(llm_cfg.get("primary_model", "gpt-4o")),
        config_snapshot={"llm": llm_cfg},
        stage=stage,
        status=ExtractionStatus.running,
    )
    db.add(run)
    db.commit()
    return run


def _finish_run(
    *,
    db: Session,
    run: ExtractionRun,
    model_used: str,
    outputs: list[dict],
    errors: list[dict],
    success_count: int,
) -> None:
    run.model_used = model_used
    run.completed_at = datetime.now(timezone.utc)
    run.raw_llm_output = {"outputs": outputs, "errors": errors}
    run.status = ExtractionStatus.completed if success_count > 0 or not errors else ExtractionStatus.failed
    if run.status == ExtractionStatus.failed and errors:
        run.error = str(errors[0].get("error", "stage_failed"))[:1000]
    db.add(run)
    db.commit()


def _extract_entities_impl(db: Session, document: Document, run: ExtractionRun, llm_cfg: dict) -> dict[str, object]:
    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
        .all()
    )
    entities = db.query(Entity).all()

    def _build(model: str, chunk: Chunk) -> str:
        return _build_extraction_prompt("entity_extraction", chunk, document)

    model_used, outputs, errors = _run_chunk_calls(
        chunks=chunks,
        stage_name="entity_extraction",
        doc_type=document.doc_type,
        llm_cfg=llm_cfg,
        build_prompt=_build,
    )

    success_count = 0
    for item in outputs:
        chunk = next((c for c in chunks if str(c.id) == item["chunk_id"]), None)
        if not chunk:
            continue
        response = item.get("response")
        if not isinstance(response, list):
            errors.append({"chunk_id": item["chunk_id"], "page_number": chunk.page_number, "error": "non_list_response"})
            continue

        for entry in response:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip() or str(entry.get("entity", "")).strip()
            if not name:
                continue
            page_number = int(entry.get("page_number", chunk.page_number))
            suggested_id = _suggest_entity_id(name, entities)
            mention = EntityMention(
                id=uuid.uuid4(),
                entity_id=None,
                document_id=document.id,
                mentioned_name=name,
                page_number=page_number,
                suggested_entity_id=suggested_id,
                resolved=False,
                resolved_by=None,
            )
            db.add(mention)
            success_count += 1
        db.commit()

    _finish_run(db=db, run=run, model_used=model_used, outputs=outputs, errors=errors, success_count=success_count)
    return {
        "run_id": str(run.id),
        "model_used": model_used,
        "selected_chunk_count": len(_select_chunks_for_stage(chunks, "entity_extraction", llm_cfg, document.doc_type)),
        "mention_count": success_count,
        "error_count": len(errors),
        "run_status": run.status.value,
    }


def _extract_obligations_impl(db: Session, document: Document, run: ExtractionRun, llm_cfg: dict) -> dict[str, object]:
    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
        .all()
    )
    entities = db.query(Entity).all()

    def _build(model: str, chunk: Chunk) -> str:
        return _build_extraction_prompt("obligation_extraction", chunk, document)

    model_used, outputs, errors = _run_chunk_calls(
        chunks=chunks,
        stage_name="obligation_extraction",
        doc_type=document.doc_type,
        llm_cfg=llm_cfg,
        build_prompt=_build,
    )
    aliases = _get_obligation_aliases(document.doc_type)

    success_count = 0
    for item in outputs:
        chunk = next((c for c in chunks if str(c.id) == item["chunk_id"]), None)
        if not chunk:
            continue
        response = item.get("response")
        if not isinstance(response, list):
            errors.append({"chunk_id": item["chunk_id"], "page_number": chunk.page_number, "error": "non_list_response"})
            continue

        for entry in response:
            if not isinstance(entry, dict):
                continue

            obligation_text = str(entry.get("quote", "")).strip()
            if not obligation_text:
                continue

            raw_obligation_type = entry.get("obligation_type")
            if isinstance(raw_obligation_type, str):
                normalized = raw_obligation_type.strip().lower()
                raw_obligation_type = aliases.get(normalized, normalized)
            obligation_type = _coerce_enum(raw_obligation_type, ObligationType, ObligationType.other)
            modality = _coerce_enum(entry.get("modality"), Modality, Modality.unknown)
            severity = _coerce_enum(entry.get("severity"), Severity, Severity.medium)
            due_kind, due_date, due_rule = _parse_due_fields(entry.get("due_date"), entry.get("due_rule"))
            responsible_entity_id = _resolve_party_entity_id(entry.get("responsible_party"), entities)

            record = Obligation(
                id=uuid.uuid4(),
                document_id=document.id,
                obligation_type=obligation_type,
                obligation_text=obligation_text,
                modality=modality,
                responsible_entity_id=responsible_entity_id,
                due_kind=due_kind,
                due_date=due_date,
                due_rule=due_rule,
                trigger_date=None,
                severity=severity,
                status=ReviewStatus.needs_review,
                system_confidence=0,
                reviewer_confidence=None,
                has_external_reference=False,
                contradiction_flag=False,
                extraction_run_id=run.id,
            )
            db.add(record)
            success_count += 1
        db.commit()

    _finish_run(db=db, run=run, model_used=model_used, outputs=outputs, errors=errors, success_count=success_count)
    return {
        "run_id": str(run.id),
        "model_used": model_used,
        "selected_chunk_count": len(
            _select_chunks_for_stage(chunks, "obligation_extraction", llm_cfg, document.doc_type)
        ),
        "obligation_count": success_count,
        "error_count": len(errors),
        "run_status": run.status.value,
    }


def _extract_risks_impl(db: Session, document: Document, run: ExtractionRun, llm_cfg: dict) -> dict[str, object]:
    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
        .all()
    )

    def _build(model: str, chunk: Chunk) -> str:
        return _build_extraction_prompt("risk_extraction", chunk, document)

    model_used, outputs, errors = _run_chunk_calls(
        chunks=chunks,
        stage_name="risk_extraction",
        doc_type=document.doc_type,
        llm_cfg=llm_cfg,
        build_prompt=_build,
    )

    success_count = 0
    for item in outputs:
        chunk = next((c for c in chunks if str(c.id) == item["chunk_id"]), None)
        if not chunk:
            continue
        response = item.get("response")
        if not isinstance(response, list):
            errors.append({"chunk_id": item["chunk_id"], "page_number": chunk.page_number, "error": "non_list_response"})
            continue

        for entry in response:
            if not isinstance(entry, dict):
                continue

            risk_text = str(entry.get("quote", "")).strip() or str(entry.get("risk_text", "")).strip()
            if not risk_text:
                continue

            risk_type = _coerce_enum(entry.get("risk_type"), RiskType, RiskType.unknown_risk)
            severity = _coerce_enum(entry.get("severity"), Severity, Severity.medium)

            record = Risk(
                id=uuid.uuid4(),
                document_id=document.id,
                risk_type=risk_type,
                risk_text=risk_text,
                severity=severity,
                status=ReviewStatus.needs_review,
                system_confidence=0,
                reviewer_confidence=None,
                has_external_reference=False,
                contradiction_flag=False,
                extraction_run_id=run.id,
            )
            db.add(record)
            success_count += 1
        db.commit()

    _finish_run(db=db, run=run, model_used=model_used, outputs=outputs, errors=errors, success_count=success_count)
    return {
        "run_id": str(run.id),
        "model_used": model_used,
        "selected_chunk_count": len(_select_chunks_for_stage(chunks, "risk_extraction", llm_cfg, document.doc_type)),
        "risk_count": success_count,
        "error_count": len(errors),
        "run_status": run.status.value,
    }


def _run_extraction_stage(
    *,
    document_id: str | uuid.UUID,
    stage: ExtractionStage,
    prompt_name: str,
    impl,
) -> dict[str, object]:
    update_parse_status(str(document_id), ParseStatus.extraction)

    db: Session = SessionLocal()
    try:
        doc_id = _to_uuid(document_id)
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            return {"document_id": str(document_id), "status": "not_found", "stage": stage.value}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": str(document.id), "status": "skipped", "stage": stage.value, "reason": "parse_failed"}

        llm_cfg = settings.raw.get("llm", {})
        run = _start_run(db=db, document=document, stage=stage, prompt_name=prompt_name, llm_cfg=llm_cfg)
        summary = impl(db, document, run, llm_cfg)
        return {
            "document_id": str(document.id),
            "status": "ok" if summary.get("error_count", 0) == 0 else "partial",
            "stage": stage.value,
            **summary,
        }
    finally:
        db.close()


def extract_entities(document_id: str) -> dict[str, object]:
    return _run_extraction_stage(
        document_id=document_id,
        stage=ExtractionStage.entity_extraction,
        prompt_name="extract_entities_default",
        impl=_extract_entities_impl,
    )


def extract_obligations(document_id: str) -> dict[str, object]:
    return _run_extraction_stage(
        document_id=document_id,
        stage=ExtractionStage.obligation_extraction,
        prompt_name="extract_obligations_default",
        impl=_extract_obligations_impl,
    )


def extract_risks(document_id: str) -> dict[str, object]:
    return _run_extraction_stage(
        document_id=document_id,
        stage=ExtractionStage.risk_extraction,
        prompt_name="extract_risks_default",
        impl=_extract_risks_impl,
    )
