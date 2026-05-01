from __future__ import annotations

from datetime import date, datetime, timezone
from difflib import SequenceMatcher
import logging
import time
import uuid

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import (
    Document,
    DocumentPage,
    DueKind,
    Entity,
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
from ...services.llm import LLMResponseError, llm_completion, parse_json_dict
from ...services.normalization import normalize_text
from ._helpers import update_parse_status
from .verify import _verify_obligations, _verify_risks

logger = logging.getLogger(__name__)


_CRITIC_PROMPT_TEMPLATE = (
    "You are a legal document auditor. Your task is to review extracted obligations "
    "and risks for accuracy, and identify any that were missed.\n\n"
    "Document type: {doc_type}\n\n"
    "PART 1 — VALIDATE EXISTING ITEMS\n"
    "Review each item below. For each, determine:\n"
    "- Is this a genuine obligation/risk from THIS specific agreement?\n"
    "- Is it correctly classified (type, severity)?\n"
    "- Should it be kept or rejected?\n\n"
    "Do NOT reject items just because they are standard/common — only reject if they "
    "are NOT actually present in the agreement text or are from an attached statutory "
    "summary rather than the agreement itself.\n\n"
    "Items to validate:\n"
    "{items_block}\n\n"
    "PART 2 — DETECT MISSED ITEMS\n"
    "Given the document text below, identify any obligations or risks that were "
    "NOT captured in the items above. Only include items that clearly impose a duty "
    "or expose a party to liability.\n\n"
    "Document text:\n"
    "{document_text}\n\n"
    "Return JSON with keys validations, new_obligations, and new_risks."
)


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _clamp_confidence(value: object) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0, min(100, int(value)))


def _coerce_enum(value: object, enum_cls, default):
    if not isinstance(value, str):
        return default
    try:
        return enum_cls(value.strip().lower())
    except Exception:
        return default


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


def _normalize_quote(text: str) -> str:
    return normalize_text(text or "").lower()


def _is_duplicate_quote(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 40 and shorter in longer and len(shorter) / max(1, len(longer)) >= 0.5:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def _suggest_entity_id(name: str, entities: list[Entity]) -> uuid.UUID | None:
    if not name.strip():
        return None
    best_score = 0.0
    best_id: uuid.UUID | None = None
    needle = name.lower()
    for entity in entities:
        candidates = [entity.canonical_name, *(entity.aliases or [])]
        for candidate in candidates:
            score = SequenceMatcher(None, needle, str(candidate).lower()).ratio()
            if score > best_score:
                best_score = score
                best_id = entity.id
    return best_id if best_score >= 0.85 else None


def _build_items_block(items: list[Obligation | Risk]) -> str:
    if not items:
        return "No existing extracted items."
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
        lines.append(
            f"{idx}. [{kind}] id={item.id} type={item_type} severity={item.severity.value} "
            f"status={item.status.value}\n"
            f'   Quote: "{(quote or "")[:600]}"'
        )
    return "\n".join(lines)


def _build_prompt(document: Document, items: list[Obligation | Risk], full_text: str) -> str:
    return _CRITIC_PROMPT_TEMPLATE.format(
        doc_type=document.doc_type.value,
        items_block=_build_items_block(items),
        document_text=full_text[:120_000],
    )


def _get_or_create_prompt_version(db: Session, uploaded_by: uuid.UUID) -> PromptVersion:
    prompt = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.prompt_name == "critic_detection_default",
            PromptVersion.is_active == True,  # noqa: E712
        )
        .order_by(PromptVersion.version.desc())
        .first()
    )
    if prompt:
        return prompt

    existing = db.query(PromptVersion).filter(PromptVersion.prompt_name == "critic_detection_default").all()
    next_version = max([p.version for p in existing], default=0) + 1

    prompt = PromptVersion(
        id=uuid.uuid4(),
        prompt_name="critic_detection_default",
        version=next_version,
        template="Critic validation and missed-item detection.",
        doc_type=None,
        description="Auto-generated default prompt for critic detection stage",
        is_active=True,
        created_by=uploaded_by,
    )
    db.add(prompt)
    db.commit()
    return prompt


def _call_critic_with_fallback(
    *,
    prompt: str,
    critic_model: str,
    llm_cfg: dict,
    batch_index: int,
    batch_size: int,
) -> tuple[str, str]:
    """Call llm_completion with model fallback + retries. Returns (model_used, raw_response).

    Tries the critic-configured model first, then any additional fallback models
    from the global llm config. Per-attempt latency and outcome are logged.
    Raises the last exception if every (model, attempt) combination fails.
    """
    fallback_models = list(llm_cfg.get("fallback_models", []))
    models = [critic_model, *[m for m in fallback_models if m != critic_model]]
    max_retries = max(1, int(llm_cfg.get("max_retries", 2)))
    backoff_base = max(1, int(llm_cfg.get("retry_backoff_base", 2)))

    last_error: Exception | None = None
    for model in models:
        for attempt in range(max_retries):
            t0 = time.monotonic()
            try:
                raw = llm_completion(model, prompt, prefer_json_object=True)
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "critic.batch ok batch=%d items=%d model=%s attempt=%d latency_ms=%d",
                    batch_index, batch_size, model, attempt, latency_ms,
                )
                return model, raw
            except Exception as exc:
                last_error = exc
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "critic.batch fail batch=%d items=%d model=%s attempt=%d latency_ms=%d error=%s",
                    batch_index, batch_size, model, attempt, latency_ms, exc.__class__.__name__,
                )
                if attempt < max_retries - 1:
                    time.sleep(backoff_base ** (attempt + 1))
        # exhausted retries on this model; fall through to next model

    raise last_error if last_error else RuntimeError("critic batch had no models to try")


def criticize_extractions(document_id: str) -> dict[str, object]:
    critic_cfg = settings.raw.get("critic", {})
    if not critic_cfg.get("enabled", False):
        return {"document_id": document_id, "status": "skipped", "reason": "disabled"}

    update_parse_status(document_id, ParseStatus.critic_review)

    db: Session = SessionLocal()
    run: ExtractionRun | None = None
    try:
        doc_id = _to_uuid(document_id)
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            return {"document_id": document_id, "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": str(document.id), "status": "skipped", "reason": "parse_failed"}

        model = str(critic_cfg.get("model", "claude-sonnet-4-6"))
        max_items = max(1, int(critic_cfg.get("max_items_per_call", 30) or 30))
        auto_reject_threshold = max(0, min(100, int(critic_cfg.get("auto_reject_threshold", 70))))
        llm_cfg = settings.raw.get("llm", {}) if isinstance(settings.raw, dict) else {}

        obligations = db.query(Obligation).filter(Obligation.document_id == document.id).all()
        risks = db.query(Risk).filter(Risk.document_id == document.id).all()
        pages = (
            db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )
        entities = db.query(Entity).all()
        full_text = "\n\n".join((page.normalized_text or page.raw_text or "") for page in pages)

        items: list[Obligation | Risk] = [*obligations, *risks]
        by_id = {str(item.id): item for item in items}

        prompt_version = _get_or_create_prompt_version(db, document.uploaded_by)
        run = ExtractionRun(
            id=uuid.uuid4(),
            document_id=document.id,
            prompt_version_id=prompt_version.id,
            model_used=model,
            config_snapshot={"critic": critic_cfg},
            stage=ExtractionStage.critic_detection,
            status=ExtractionStatus.running,
        )
        db.add(run)
        db.commit()

        validations_applied = 0
        auto_rejected = 0
        outputs: list[dict] = []
        errors: list[dict] = []
        success_count = 0
        all_new_obligation_entries: list[dict] = []
        all_new_risk_entries: list[dict] = []

        batches: list[list[Obligation | Risk]] = []
        if items:
            for start in range(0, len(items), max_items):
                batches.append(items[start : start + max_items])
        else:
            batches = [[]]

        for batch_index, batch in enumerate(batches):
            prompt = _build_prompt(document, batch, full_text)

            try:
                model_used, raw = _call_critic_with_fallback(
                    prompt=prompt,
                    critic_model=model,
                    llm_cfg=llm_cfg,
                    batch_index=batch_index,
                    batch_size=len(batch),
                )
            except Exception as exc:
                errors.append({
                    "batch_index": batch_index,
                    "item_count": len(batch),
                    "error": f"{exc.__class__.__name__}: {exc}"[:500],
                })
                logger.error(
                    "critic.batch exhausted batch=%d items=%d error=%s",
                    batch_index, len(batch), exc.__class__.__name__,
                )
                continue

            try:
                payload = parse_json_dict(raw)
            except LLMResponseError as exc:
                errors.append({
                    "batch_index": batch_index,
                    "item_count": len(batch),
                    "error": f"parse: {exc}"[:500],
                })
                logger.error("critic.batch parse_failed batch=%d error=%s", batch_index, exc)
                continue

            outputs.append({"batch_index": batch_index, "model": model_used, "response": payload})
            success_count += 1

            validations = payload.get("validations", [])
            if isinstance(validations, list):
                for row in validations:
                    if not isinstance(row, dict):
                        continue
                    item = by_id.get(str(row.get("id", "")))
                    if item is None:
                        continue

                    valid = row.get("valid")
                    if not isinstance(valid, bool):
                        continue
                    confidence = _clamp_confidence(row.get("confidence"))
                    reasoning = str(row.get("reasoning", "")).strip() or None

                    item.critic_valid = valid
                    item.critic_confidence = confidence
                    item.critic_reasoning = reasoning
                    if valid is False and confidence is not None and confidence >= auto_reject_threshold:
                        if item.status != ReviewStatus.rejected:
                            item.status = ReviewStatus.rejected
                            auto_rejected += 1
                    db.add(item)
                    validations_applied += 1
                db.commit()

            new_obligations = payload.get("new_obligations", [])
            if isinstance(new_obligations, list):
                all_new_obligation_entries.extend([row for row in new_obligations if isinstance(row, dict)])

            new_risks = payload.get("new_risks", [])
            if isinstance(new_risks, list):
                all_new_risk_entries.extend([row for row in new_risks if isinstance(row, dict)])

        existing_obligation_quotes = [_normalize_quote(row.obligation_text or "") for row in obligations]
        existing_risk_quotes = [_normalize_quote(row.risk_text or "") for row in risks]

        new_obligations_rows: list[Obligation] = []
        seen_obligation_quotes: list[str] = []
        for entry in all_new_obligation_entries:
            quote = str(entry.get("quote", "")).strip()
            normalized_quote = _normalize_quote(quote)
            if not normalized_quote:
                continue
            if any(_is_duplicate_quote(normalized_quote, existing) for existing in existing_obligation_quotes):
                continue
            if any(_is_duplicate_quote(normalized_quote, existing) for existing in seen_obligation_quotes):
                continue

            obligation_type = _coerce_enum(entry.get("obligation_type"), ObligationType, ObligationType.other)
            modality = _coerce_enum(entry.get("modality"), Modality, Modality.unknown)
            severity = _coerce_enum(entry.get("severity"), Severity, Severity.medium)
            due_kind, due_date, due_rule = _parse_due_fields(entry.get("due_date"), entry.get("due_rule"))
            responsible_entity_id = _suggest_entity_id(str(entry.get("responsible_party", "")).strip(), entities)

            row = Obligation(
                id=uuid.uuid4(),
                document_id=document.id,
                obligation_type=obligation_type,
                obligation_text=quote,
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
                critic_valid=True,
                critic_confidence=100,
                critic_reasoning="Detected by critic stage",
                has_external_reference=False,
                contradiction_flag=False,
                extraction_run_id=run.id,
            )
            db.add(row)
            new_obligations_rows.append(row)
            seen_obligation_quotes.append(normalized_quote)
        db.commit()

        new_risk_rows: list[Risk] = []
        seen_risk_quotes: list[str] = []
        for entry in all_new_risk_entries:
            quote = str(entry.get("quote", "")).strip() or str(entry.get("risk_text", "")).strip()
            normalized_quote = _normalize_quote(quote)
            if not normalized_quote:
                continue
            if any(_is_duplicate_quote(normalized_quote, existing) for existing in existing_risk_quotes):
                continue
            if any(_is_duplicate_quote(normalized_quote, existing) for existing in seen_risk_quotes):
                continue

            risk_type = _coerce_enum(entry.get("risk_type"), RiskType, RiskType.unknown_risk)
            severity = _coerce_enum(entry.get("severity"), Severity, Severity.medium)
            row = Risk(
                id=uuid.uuid4(),
                document_id=document.id,
                risk_type=risk_type,
                risk_text=quote,
                severity=severity,
                status=ReviewStatus.needs_review,
                system_confidence=0,
                reviewer_confidence=None,
                critic_valid=True,
                critic_confidence=100,
                critic_reasoning="Detected by critic stage",
                has_external_reference=False,
                contradiction_flag=False,
                extraction_run_id=run.id,
            )
            db.add(row)
            new_risk_rows.append(row)
            seen_risk_quotes.append(normalized_quote)
        db.commit()

        # Verify the new items, then drop any that ended up without evidence.
        # _verify_* now pre-loads existing evidence keys, so a critic-detected
        # duplicate of an existing obligation/risk dedups to no evidence — we
        # delete those orphans here so the no-claim-without-evidence guarantee
        # holds.
        orphan_new_obligations = 0
        if new_obligations_rows:
            new_ob_evidence, _ = _verify_obligations(db, document, pages, new_obligations_rows)
            for ob in list(new_obligations_rows):
                if ob.id not in new_ob_evidence:
                    db.delete(ob)
                    orphan_new_obligations += 1
            if orphan_new_obligations:
                db.commit()
                logger.info(
                    "critic.cleanup deleted %d orphan obligation(s) without evidence",
                    orphan_new_obligations,
                )

        orphan_new_risks = 0
        if new_risk_rows:
            new_risk_evidence, _ = _verify_risks(db, document, pages, new_risk_rows)
            for rk in list(new_risk_rows):
                if rk.id not in new_risk_evidence:
                    db.delete(rk)
                    orphan_new_risks += 1
            if orphan_new_risks:
                db.commit()
                logger.info(
                    "critic.cleanup deleted %d orphan risk(s) without evidence",
                    orphan_new_risks,
                )

        run.completed_at = datetime.now(timezone.utc)
        run.raw_llm_output = {"outputs": outputs, "errors": errors}
        run.status = ExtractionStatus.completed if (success_count > 0 or not errors) else ExtractionStatus.failed
        if run.status == ExtractionStatus.failed and errors:
            run.error = str(errors[0].get("error", "stage_failed"))[:1000]
        db.add(run)
        db.commit()

        return {
            "document_id": str(document.id),
            "status": "ok" if not errors else "partial",
            "model_used": model,
            "run_id": str(run.id),
            "run_status": run.status.value,
            "batch_count": len(batches),
            "successful_batch_count": success_count,
            "failed_batch_count": len(errors),
            "validated_count": validations_applied,
            "auto_rejected_count": auto_rejected,
            "new_obligation_count": len(new_obligations_rows) - orphan_new_obligations,
            "new_risk_count": len(new_risk_rows) - orphan_new_risks,
            "orphan_obligation_count": orphan_new_obligations,
            "orphan_risk_count": orphan_new_risks,
        }
    except Exception as exc:
        db.rollback()
        if run is not None:
            run.completed_at = datetime.now(timezone.utc)
            run.status = ExtractionStatus.failed
            run.error = str(exc)[:1000]
            db.add(run)
            db.commit()
        return {"document_id": str(document_id), "status": "failed", "error": str(exc)[:200]}
    finally:
        db.close()
