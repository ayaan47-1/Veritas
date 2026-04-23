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
from ...services.normalization import normalize_text
from ._helpers import update_parse_status

logger = logging.getLogger(__name__)

_WARNED_MISSING_DOMAINS = False


def call_extract_llm(*, model: str, prompt: str, stage: str) -> list[dict]:
    """Call LLM for extraction via LiteLLM."""
    from ...services.llm import extract as _llm_extract

    return _llm_extract(model=model, prompt=prompt, stage=stage)


def call_classify_llm(*, model: str, prompt: str) -> dict:
    """Call LLM for clause classification via LiteLLM. Returns parsed dict."""
    from ...services.llm import llm_completion, parse_json_dict

    raw = llm_completion(model, prompt, prefer_json_object=True)
    return parse_json_dict(raw)


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
    "You are an expert contract analyst. Extract every obligation from the text below.\n\n"
    "AN OBLIGATION IS ANY CLAUSE THAT CONSTRAINS A PARTY'S CONDUCT. This includes "
    "BOTH:\n"
    "  (A) AFFIRMATIVE DUTIES — things a party must do "
    "('Resident shall pay', 'Tenant must notify', 'Contractor will submit').\n"
    "  (B) PROHIBITIONS AND RESTRICTIONS — things a party must NOT do, or may only "
    "do under limits. These are duties of forbearance and are obligations. Trigger "
    "phrases include: 'shall not', 'may not', 'must not', 'is prohibited', "
    "'are prohibited from', 'no [X] shall', 'refrain from', 'agrees not to', "
    "'only', 'solely', 'exclusively', 'without prior written consent', 'unless'.\n"
    "  Examples of prohibition-style obligations that MUST be extracted:\n"
    "    - 'No portion of the rental unit shall be sublet.'\n"
    "    - 'Resident may not install any security devices.'\n"
    "    - 'Smoking of any substance is prohibited everywhere on the premises.'\n"
    "    - 'Resident shall refrain from storing gasoline in the unit.'\n"
    "    - 'Resident shall only use assigned parking spaces.'\n"
    "    - 'The rental unit shall be used as a dwelling for residential purposes only.'\n\n"
    "SEVERITY DEFINITIONS (use these exactly):\n"
    "- critical: financial penalty clause, liquidated damages, indemnification, "
    "termination rights, bond/insurance requirements with termination consequences\n"
    "- high: mandatory compliance with statute/regulation, hard deadlines with "
    "consequences, OSHA/safety requirements, safety-related prohibitions "
    "(flammables, smoking, fire code)\n"
    "- medium: standard contractual duty (shall/must) or prohibition (shall not/"
    "may not) without direct penalty language, payment terms, submission "
    "requirements, use restrictions\n"
    "- low: procedural or administrative duties, notice requirements, "
    "record-keeping, formatting requirements\n\n"
    "OBLIGATION TYPES:\n"
    "- payment: monetary obligations, invoicing, retention, payment schedules\n"
    "- submission: deliverables, submittals, reports, documents to be provided\n"
    "- notification: notice requirements, written notice, communication obligations\n"
    "- compliance: regulatory compliance, standards adherence, code compliance, "
    "PROHIBITIONS AND USE RESTRICTIONS (no subletting, no smoking, no alterations, "
    "parking restrictions, permitted-use limits) — when in doubt, prohibitions "
    "belong here.\n"
    "- inspection: site visits, quality checks, testing, audits\n"
    "- other: obligations not fitting the above categories\n\n"
    "INSTRUCTIONS:\n"
    "1. Extract every clause that imposes EITHER an affirmative duty OR a "
    "prohibition/restriction on a named or implied party. Do not skip a clause "
    "merely because it is phrased in the negative.\n"
    "2. Do NOT extract from summary sections, informational disclosures, "
    "or sections that merely restate obligations found elsewhere in the document.\n"
    "3. Do NOT extract general statements of law or rights — only extract specific "
    "contractual obligations from THIS agreement.\n"
    "4. Quote the EXACT wording from the text (verbatim, not paraphrased). "
    "Each quote must be 1-3 complete sentences copied directly from the text.\n"
    "5. Assign severity using the definitions above. Be decisive.\n"
    "6. Do NOT extract preamble, definitions, or recitals that do not impose a "
    "duty or restriction.\n"
    "7. Do NOT paraphrase or summarize — copy the exact words.\n"
    "8. When a clause contains both an affirmative duty and a prohibition, extract "
    "each as a separate obligation if they are distinct sentences.\n\n"
    "For each obligation return a JSON object with these exact fields:\n"
    '  "quote": verbatim sentence(s) from the text that state the obligation (required),\n'
    '  "obligation_type": one of payment|submission|notification|compliance|inspection|other,\n'
    '  "modality": one of must|shall|will|should|may|unknown,\n'
    '  "severity": one of low|medium|high|critical,\n'
    '  "due_date": ISO date string or null,\n'
    '  "due_rule": relative deadline description or null,\n'
    '  "responsible_party": name of the obligor or null.\n'
    "Return a JSON array only. Return [] if no obligations found."
)

_RISK_SCHEMA = (
    "You are an expert contract analyst. Extract every risk, liability, or penalty "
    "clause from the text below.\n\n"
    "A RISK IS ANY CLAUSE THAT EXPOSES A PARTY TO LIABILITY, PENALTY, FINANCIAL "
    "LOSS, OR ADVERSE CONSEQUENCE. This explicitly INCLUDES prohibitions whose "
    "breach creates exposure \u2014 safety prohibitions (flammables, smoking, "
    "fire code, battery-charging), legal-violation prohibitions (criminal/civil "
    "law, local ordinances), and damage-liability clauses. A clause like "
    "'Resident shall refrain from storing gasoline in the unit' is BOTH an "
    "obligation and a risk \u2014 extract it here as a risk (safety) because "
    "breach creates injury and liability exposure.\n\n"
    "SEVERITY DEFINITIONS (use these exactly):\n"
    "- critical: financial penalty clause, liquidated damages, indemnification, "
    "termination rights, bond forfeiture, personal liability exposure\n"
    "- high: breach of contract consequences, acceleration clauses, foreclosure "
    "triggers, safety violation consequences, safety prohibitions (flammables, "
    "fire code, smoking where prohibited), law-violation prohibitions\n"
    "- medium: standard risk allocation clauses, insurance requirements, warranty "
    "limitations, schedule delay provisions, damage-to-premises liability\n"
    "- low: procedural non-compliance risks, administrative penalties, minor "
    "reporting failures\n\n"
    "RISK TYPES:\n"
    "- financial: monetary penalties, damages, cost overruns, payment disputes\n"
    "- schedule: delays, missed milestones, time-at-large claims\n"
    "- quality: defects, rework, warranty claims, non-conformance\n"
    "- safety: OSHA violations, injury liability, hazardous materials\n"
    "- compliance: regulatory violations, permit failures, code non-compliance\n"
    "- contractual: breach, termination, default, indemnification\n"
    "- unknown_risk: risks not fitting the above categories\n\n"
    "INSTRUCTIONS:\n"
    "1. Extract every clause that exposes a party to liability, penalty, or "
    "financial loss \u2014 including safety prohibitions, legal-violation "
    "prohibitions, and damage-liability clauses. Do not skip prohibitions: a "
    "clause whose breach creates injury or legal liability IS a risk.\n"
    "2. Do NOT extract from summary sections, informational disclosures, "
    "or sections that merely restate risks found elsewhere in the document.\n"
    "3. Do NOT extract general statements of law — only extract specific risk "
    "clauses from THIS agreement.\n"
    "4. Quote the EXACT wording from the text (verbatim, not paraphrased). "
    "Each quote must be 1-3 complete sentences copied directly from the text.\n"
    "5. Assign severity using the definitions above. Be decisive.\n"
    "6. Do NOT extract general statements that merely define terms without imposing risk.\n"
    "7. Do NOT paraphrase or summarize — copy the exact words.\n\n"
    "For each risk return a JSON object with these exact fields:\n"
    '  "quote": verbatim sentence(s) from the text describing the risk (required),\n'
    '  "risk_type": one of financial|schedule|quality|safety|compliance|contractual|unknown_risk,\n'
    '  "severity": one of low|medium|high|critical.\n'
    "Return a JSON array only. Return [] if no risks found."
)

_CLASSIFY_SCHEMA = (
    "You are an expert contract analyst. Your task is to walk through EVERY "
    "clause, paragraph, and provision in the document below and classify each one.\n\n"
    "For EACH distinct clause or paragraph, decide:\n"
    '- "obligation" \u2014 CONSTRAINS A PARTY\'S CONDUCT. This covers BOTH '
    "affirmative duties ('Resident shall pay', 'must notify', 'will submit') AND "
    "prohibitions or use restrictions ('shall not', 'may not', 'is prohibited', "
    "'refrain from', 'no [X] shall', 'only', 'solely', 'without prior written "
    "consent'). Prohibitions ARE obligations \u2014 do not skip a clause merely "
    "because it is phrased in the negative. Example prohibitions that must be "
    "classified as obligations: 'No portion of the rental unit shall be sublet.' / "
    "'Resident may not install any security devices.' / 'Smoking is prohibited "
    "everywhere on the premises.' / 'Resident shall refrain from storing gasoline.' / "
    "'Resident shall only use assigned parking spaces.'\n"
    '- "risk" \u2014 exposes a party to liability, penalty, financial loss, or '
    "adverse consequence. This explicitly INCLUDES prohibitions whose breach "
    "creates safety, legal, or financial exposure \u2014 e.g. storing flammables, "
    "smoking where prohibited, violating criminal/civil law, fire-code "
    "violations, battery-charging hazards, damage-to-premises clauses.\n"
    '- "both" \u2014 imposes a duty (affirmative OR prohibition) AND exposes a '
    "party to risk. MOST safety prohibitions, legal-violation prohibitions, and "
    "damage/liability clauses should be classified as \"both\" \u2014 they are "
    "obligations because they constrain conduct, AND they are risks because "
    "breach creates liability. Examples that MUST be \"both\":\n"
    "    - 'Resident shall refrain from storing gasoline or flammable liquids.'\n"
    "    - 'Smoking of any substance is prohibited everywhere on the premises.'\n"
    "    - 'Resident shall not violate any criminal or civil law.'\n"
    "    - 'Repair or maintenance of batteries and motors is prohibited within the rental unit.'\n"
    "    - 'Resident shall pay Landlord for costs to repair any damage to the premises.'\n"
    '- "neither" \u2014 definitions, recitals, preamble, boilerplate, or informational text\n\n'
    "OBLIGATION SEVERITY:\n"
    "- critical: financial penalty clause, liquidated damages, indemnification, "
    "termination rights, bond/insurance requirements with termination consequences\n"
    "- high: mandatory compliance with statute/regulation, hard deadlines with "
    "consequences, OSHA/safety requirements, safety-related prohibitions "
    "(flammables, smoking, fire code, micromobility battery storage)\n"
    "- medium: standard contractual duty (shall/must) or prohibition (shall not/"
    "may not) without direct penalty language, payment terms, submission "
    "requirements, use restrictions\n"
    "- low: procedural or administrative duties, notice requirements, record-keeping, "
    "formatting requirements\n\n"
    "RISK SEVERITY:\n"
    "- critical: financial penalty clause, liquidated damages, indemnification, "
    "termination rights, bond forfeiture, personal liability exposure\n"
    "- high: breach of contract consequences, acceleration clauses, foreclosure "
    "triggers, safety violation consequences\n"
    "- medium: standard risk allocation clauses, insurance requirements, warranty "
    "limitations, schedule delay provisions\n"
    "- low: procedural non-compliance risks, administrative penalties, minor "
    "reporting failures\n\n"
    "OBLIGATION TYPES: payment | submission | notification | compliance | inspection | other\n"
    "RISK TYPES: financial | schedule | quality | safety | compliance | contractual | unknown_risk\n\n"
    "INSTRUCTIONS:\n"
    "1. Read through the ENTIRE document systematically, clause by clause.\n"
    "2. For each clause classified as \"obligation\" or \"both\", extract it as an "
    "obligation. Prohibitions and restrictions count as obligations \u2014 do not "
    "skip negative-voice clauses ('shall not', 'may not', 'is prohibited', "
    "'refrain from', 'no X shall'). When a clause contains both an affirmative duty "
    "and a prohibition as distinct sentences, extract each separately.\n"
    "3. For each clause classified as \"risk\" or \"both\", extract it as a risk.\n"
    "4. Quote the EXACT wording from the text (verbatim, 1-3 complete sentences).\n"
    "5. Skip clauses classified as \"neither\" \u2014 do not include them.\n"
    "6. Do NOT extract from attached statutory summaries or tenant rights disclosures "
    "\u2014 only from the agreement itself.\n"
    "7. Do NOT paraphrase or summarize \u2014 copy the exact words.\n\n"
    "Return ONLY valid JSON in this exact shape:\n"
    "{{\n"
    '  "obligations": [\n'
    "    {{\n"
    '      "quote": "<verbatim text>",\n'
    '      "obligation_type": "<payment|submission|notification|compliance|inspection|other>",\n'
    '      "modality": "<must|shall|will|should|may|unknown>",\n'
    '      "severity": "<low|medium|high|critical>",\n'
    '      "due_date": null,\n'
    '      "due_rule": null,\n'
    '      "responsible_party": null\n'
    "    }}\n"
    "  ],\n"
    '  "risks": [\n'
    "    {{\n"
    '      "quote": "<verbatim text>",\n'
    '      "risk_type": "<financial|schedule|quality|safety|compliance|contractual|unknown_risk>",\n'
    '      "severity": "<low|medium|high|critical>"\n'
    "    }}\n"
    "  ]\n"
    "}}\n\n"
    "Document type: {doc_type}\n"
    "Pages: {first_page}\u2013{last_page}\n\n"
    "DOCUMENT TEXT:\n"
    "{full_text}"
)

_ENTITY_SCHEMA = (
    "You are an expert contract analyst. Extract every named entity (person, company, "
    "organization, location) from the text below.\n\n"
    "INSTRUCTIONS:\n"
    "1. Extract EVERY named entity — err on the side of inclusion.\n"
    "2. Use the EXACT name or value as it appears in the text.\n"
    "3. Do NOT extract generic role references (e.g. 'the contractor') unless "
    "they are defined as a specific named party.\n\n"
    "For each entity return a JSON object with these exact fields:\n"
    '  "entity_type": one of person|organization|location|agreement_date|other,\n'
    '  "entity_value": the exact name or value as it appears in the text,\n'
    '  "location": brief description of where in the text (e.g. "Section 1").\n'
    "Return a JSON array only. Return [] if no entities found."
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


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence of two token lists."""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n]


def _rouge_l(a: list[str], b: list[str]) -> float:
    """ROUGE-L F1 score between two token lists."""
    if not a or not b:
        return 0.0
    lcs = _lcs_length(a, b)
    precision = lcs / len(b)
    recall = lcs / len(a)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


_EXTRACTION_DEDUP_MIN_CHARS = 40
_EXTRACTION_DEDUP_CONTAINMENT_RATIO = 0.5
_EXTRACTION_DEDUP_ROUGE_L_THRESHOLD = 0.6
_EXTRACTION_DEDUP_SEQUENCE_THRESHOLD = 0.8


def _normalize_extraction_quote(text: str) -> str:
    return normalize_text(text or "").lower()


def _is_duplicate_extraction_quote(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True

    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if (
        len(shorter) >= _EXTRACTION_DEDUP_MIN_CHARS
        and len(longer) > 0
        and (len(shorter) / len(longer)) >= _EXTRACTION_DEDUP_CONTAINMENT_RATIO
        and shorter in longer
    ):
        return True

    if SequenceMatcher(None, a, b).ratio() >= _EXTRACTION_DEDUP_SEQUENCE_THRESHOLD:
        return True

    return _rouge_l(_word_tokens(a), _word_tokens(b)) >= _EXTRACTION_DEDUP_ROUGE_L_THRESHOLD


def _has_metadata_conflict(a: dict[str, object], b: dict[str, object]) -> bool:
    """Guard against merging candidates with materially different metadata."""
    if a.get("due_date") and b.get("due_date") and a["due_date"] != b["due_date"]:
        return True
    if a.get("responsible_entity_id") and b.get("responsible_entity_id") and a["responsible_entity_id"] != b["responsible_entity_id"]:
        return True
    return False


def _obligation_candidate_score(candidate: dict[str, object]) -> int:
    score = 0
    if candidate.get("due_kind") != DueKind.none:
        score += 2
    if candidate.get("due_date") is not None:
        score += 2
    if candidate.get("due_rule"):
        score += 1
    if candidate.get("responsible_entity_id") is not None:
        score += 1
    modality = candidate.get("modality")
    if modality in {Modality.must, Modality.shall, Modality.required}:
        score += 1
    return score


def _risk_candidate_score(candidate: dict[str, object]) -> int:
    score = 0
    if candidate.get("risk_type") != RiskType.unknown_risk:
        score += 1
    if candidate.get("severity") in {Severity.high, Severity.critical}:
        score += 1
    return score


def _dedupe_candidates(
    candidates: list[dict[str, object]],
    *,
    text_key: str,
    score_fn,
) -> tuple[list[dict[str, object]], int]:
    unique_candidates: list[dict[str, object]] = []
    unique_quotes: list[str] = []
    removed = 0

    for candidate in candidates:
        quote = _normalize_extraction_quote(str(candidate.get(text_key, "")))
        if not quote:
            continue

        duplicate_idx = -1
        for idx, existing_quote in enumerate(unique_quotes):
            if _is_duplicate_extraction_quote(quote, existing_quote) and not _has_metadata_conflict(candidate, unique_candidates[idx]):
                duplicate_idx = idx
                break

        if duplicate_idx < 0:
            unique_candidates.append(candidate)
            unique_quotes.append(quote)
            continue

        removed += 1
        if score_fn(candidate) > score_fn(unique_candidates[duplicate_idx]):
            unique_candidates[duplicate_idx] = candidate
            unique_quotes[duplicate_idx] = quote

    return unique_candidates, removed


def _filter_agreement_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Return only chunks labeled as agreement_body (or unlabeled)."""
    return [c for c in chunks if c.section_label in (None, "agreement_body")]


def _section_filter_settings(extraction_cfg: dict) -> tuple[float, bool]:
    """Return (bypass_ratio_threshold, retry_on_zero_results)."""
    section_filter_cfg = extraction_cfg.get("section_filter", {}) if isinstance(extraction_cfg, dict) else {}

    threshold_raw = section_filter_cfg.get("max_non_agreement_ratio_before_bypass", 0.9)
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        threshold = 0.9
    threshold = max(0.0, min(1.0, threshold))

    retry_on_zero_results = bool(section_filter_cfg.get("retry_all_chunks_on_zero_results", True))
    return threshold, retry_on_zero_results


def _select_chunks_with_section_filter_guardrails(
    *,
    all_chunks: list[Chunk],
    max_non_agreement_ratio_before_bypass: float,
    force_all_chunks: bool = False,
    stage_name: str = "unknown_stage",
) -> tuple[list[Chunk], dict[str, object], bool, str]:
    """Apply section-filter guardrails and return chunk selection metadata.

    Returns:
      (selected_chunks, section_filter_stats, section_filter_bypassed, chunk_source)
    """
    filtered_chunks = _filter_agreement_chunks(all_chunks)

    total_chunks = len(all_chunks)
    agreement_chunks = len(filtered_chunks)
    non_agreement_chunks = max(0, total_chunks - agreement_chunks)
    non_agreement_ratio = (non_agreement_chunks / total_chunks) if total_chunks else 0.0

    section_filter_stats = {
        "total": total_chunks,
        "agreement": agreement_chunks,
        "non_agreement": non_agreement_chunks,
        "non_agreement_ratio": round(non_agreement_ratio, 4),
    }

    if force_all_chunks:
        if total_chunks:
            logger.warning(
                "[%s] Forcing all-chunks extraction override (%d chunks)",
                stage_name,
                total_chunks,
            )
        return all_chunks, section_filter_stats, bool(total_chunks), "all_chunks_fallback"

    if total_chunks and not filtered_chunks:
        logger.warning(
            "[%s] All chunks filtered as non_agreement; using all chunks fallback (%d chunks)",
            stage_name,
            total_chunks,
        )
        return all_chunks, section_filter_stats, True, "all_chunks_fallback"

    if (
        total_chunks
        and filtered_chunks
        and non_agreement_ratio >= max_non_agreement_ratio_before_bypass
    ):
        logger.warning(
            "[%s] High non_agreement ratio %.3f (threshold %.3f); using all chunks fallback (%d/%d non_agreement)",
            stage_name,
            non_agreement_ratio,
            max_non_agreement_ratio_before_bypass,
            non_agreement_chunks,
            total_chunks,
        )
        return all_chunks, section_filter_stats, True, "all_chunks_fallback"

    return filtered_chunks, section_filter_stats, False, "filtered"


def _token_set(text: str) -> set[str]:
    """Bag-of-words tokens for MMR chunk similarity (order-independent)."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


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


def _group_chunks(chunks: list[Chunk], group_size: int) -> list[list[Chunk]]:
    """Split chunks into groups of up to group_size."""
    if group_size <= 1:
        return [[c] for c in chunks]
    return [chunks[i:i + group_size] for i in range(0, len(chunks), group_size)]


def _build_grouped_extraction_prompt(stage_name: str, chunks: list[Chunk], document: Document) -> str:
    schema = _STAGE_SCHEMAS.get(stage_name, "Return strict JSON array only.")
    vocab_preamble = _get_vocab_preamble(stage_name, document.doc_type)
    preamble = f"{vocab_preamble}\n\n" if vocab_preamble else ""
    pages = [c.page_number for c in chunks if c.page_number is not None]
    page_range = f"{min(pages)}–{max(pages)}" if pages else "unknown"
    chunk_texts = "\n\n".join(
        f"--- Page {c.page_number} ---\n{c.text}" for c in chunks
    )
    return (
        f"Document type: {document.doc_type.value}\n"
        f"Pages: {page_range}\n\n"
        f"{preamble}"
        "The text below comes from multiple sections of the same document. "
        "Extract from ALL sections. Do NOT duplicate items that appear across "
        "page boundaries or are restated in different sections.\n\n"
        f"{schema}\n\n"
        f"Chunk text:\n{chunk_texts}"
    )


def _build_classify_prompt(chunks: list[Chunk], document: Document) -> str:
    """Build a clause-classification prompt for combined obligation+risk extraction."""
    vocab_preamble = _get_vocab_preamble("obligation_extraction", document.doc_type)
    preamble = f"{vocab_preamble}\n\n" if vocab_preamble else ""
    pages = [c.page_number for c in chunks if c.page_number is not None]
    first_page = min(pages) if pages else 1
    last_page = max(pages) if pages else 1
    chunk_texts = "\n\n".join(
        f"--- Page {c.page_number} ---\n{c.text}" for c in chunks
    )
    return (
        f"{preamble}"
        + _CLASSIFY_SCHEMA.format(
            doc_type=document.doc_type.value,
            first_page=first_page,
            last_page=last_page,
            full_text=chunk_texts,
        )
    )


def _estimate_token_count(chunks: list[Chunk], chars_per_token: int = 4) -> int:
    """Estimate the number of tokens in the concatenated chunk text."""
    return sum(len(c.text or "") for c in chunks) // max(1, chars_per_token)


def _should_use_full_doc(chunks: list[Chunk], extraction_cfg: dict) -> bool:
    """Decide whether to use full-document extraction mode."""
    mode = str(extraction_cfg.get("mode", "auto")).strip().lower()
    if mode == "chunked":
        return False
    if mode == "full_doc":
        return True
    # mode == "auto": estimate tokens, compare to threshold
    chars_per_token = int(extraction_cfg.get("chars_per_token", 4) or 4)
    threshold = int(extraction_cfg.get("full_doc_token_threshold", 150_000) or 150_000)
    estimated = _estimate_token_count(chunks, chars_per_token) + 1500  # prompt overhead
    return estimated <= threshold


def _run_full_doc_call(
    *,
    chunks: list[Chunk],
    stage_name: str,
    document: Document,
    llm_cfg: dict,
) -> tuple[str, list[dict], list[dict]]:
    """Single LLM call with all chunks concatenated. Raises RuntimeError on total failure."""
    prompt = _build_grouped_extraction_prompt(stage_name, chunks, document)
    models = [llm_cfg.get("primary_model", "gpt-4o")] + list(llm_cfg.get("fallback_models", []))
    max_retries = max(1, int(llm_cfg.get("max_retries", 3)))
    backoff_base = max(1, int(llm_cfg.get("retry_backoff_base", 2)))

    last_error: Exception | None = None
    for model in models:
        for attempt in range(max_retries):
            try:
                response = call_extract_llm(model=model, prompt=prompt, stage=stage_name)
                logger.info("Full-doc extraction succeeded for %s using %s", stage_name, model)
                return model, [{"chunk_id": str(chunks[0].id), "model": model, "response": response}], []
            except Exception as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    time.sleep(backoff_base ** (attempt + 1))

    raise RuntimeError(f"Full-doc extraction failed for {stage_name}: {last_error}")


def _run_full_doc_classify_call(
    *,
    chunks: list[Chunk],
    document: Document,
    llm_cfg: dict,
) -> tuple[str, dict, list[dict]]:
    """Single LLM call for clause classification. Returns (model_used, response_dict, errors).

    Raises RuntimeError on total failure.
    """
    prompt = _build_classify_prompt(chunks, document)
    models = [llm_cfg.get("primary_model", "gpt-4o")] + list(llm_cfg.get("fallback_models", []))
    max_retries = max(1, int(llm_cfg.get("max_retries", 3)))
    backoff_base = max(1, int(llm_cfg.get("retry_backoff_base", 2)))

    last_error: Exception | None = None
    for model in models:
        for attempt in range(max_retries):
            try:
                response = call_classify_llm(model=model, prompt=prompt)
                logger.info("Full-doc classify succeeded using %s", model)
                return model, response, []
            except Exception as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    time.sleep(backoff_base ** (attempt + 1))

    raise RuntimeError(f"Full-doc classify call failed: {last_error}")


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


def _run_grouped_chunk_calls(
    *,
    chunks: list[Chunk],
    stage_name: str,
    doc_type: DocumentType,
    llm_cfg: dict,
    build_prompt,
    build_group_prompt,
):
    """Run extraction with chunk grouping. Falls back to per-chunk on group failure."""
    selection_cfg = llm_cfg.get("chunk_selection", {}) if isinstance(llm_cfg, dict) else {}
    group_size = int(selection_cfg.get("chunks_per_group", 1) or 1)

    if group_size <= 1:
        return _run_chunk_calls(
            chunks=chunks,
            stage_name=stage_name,
            doc_type=doc_type,
            llm_cfg=llm_cfg,
            build_prompt=build_prompt,
        )

    models = [llm_cfg.get("primary_model", "gpt-4o")] + list(llm_cfg.get("fallback_models", []))
    max_retries = max(1, int(llm_cfg.get("max_retries", 3)))
    backoff_base = max(1, int(llm_cfg.get("retry_backoff_base", 2)))

    errors: list[dict] = []
    outputs: list[dict] = []
    active_model_idx = 0
    active_model = models[0] if models else "gpt-4o"

    ordered_chunks = _select_chunks_for_stage(chunks, stage_name, llm_cfg, doc_type)
    groups = _group_chunks(ordered_chunks, group_size)

    for group in groups:
        group_done = False
        last_error: Exception | None = None

        while active_model_idx < len(models) and not group_done:
            model = models[active_model_idx]
            active_model = model
            prompt = build_group_prompt(model, group)

            for attempt in range(max_retries):
                try:
                    response = call_extract_llm(model=model, prompt=prompt, stage=stage_name)
                    outputs.append({"chunk_id": str(group[0].id), "model": model, "response": response})
                    group_done = True
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        time.sleep(backoff_base ** (attempt + 1))

            if not group_done:
                active_model_idx += 1

        if not group_done:
            # Fall back to per-chunk calls for this group
            active_model_idx = 0  # reset — failure may be prompt-length related
            for chunk in group:
                chunk_done = False
                chunk_last_error: Exception | None = None

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
                            chunk_last_error = exc
                            if attempt < max_retries - 1:
                                time.sleep(backoff_base ** (attempt + 1))

                    if not chunk_done:
                        active_model_idx += 1

                if not chunk_done:
                    errors.append(
                        {
                            "chunk_id": str(chunk.id),
                            "page_number": chunk.page_number,
                            "error": str(chunk_last_error) if chunk_last_error else "unknown_error",
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
        config_snapshot={"llm": llm_cfg, "extraction": settings.raw.get("extraction", {})},
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

    extraction_cfg = settings.raw.get("extraction", {})
    use_full_doc = _should_use_full_doc(chunks, extraction_cfg)

    if use_full_doc:
        try:
            model_used, outputs, errors = _run_full_doc_call(
                chunks=chunks, stage_name="entity_extraction",
                document=document, llm_cfg=llm_cfg,
            )
        except Exception:
            logger.warning("Full-doc extraction failed for entities, falling back to chunked")
            use_full_doc = False

    if not use_full_doc:
        def _build(model: str, chunk: Chunk) -> str:
            return _build_extraction_prompt("entity_extraction", chunk, document)

        def _build_group(model: str, group: list[Chunk]) -> str:
            return _build_grouped_extraction_prompt("entity_extraction", group, document)

        model_used, outputs, errors = _run_grouped_chunk_calls(
            chunks=chunks,
            stage_name="entity_extraction",
            doc_type=document.doc_type,
            llm_cfg=llm_cfg,
            build_prompt=_build,
            build_group_prompt=_build_group,
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
        "selected_chunk_count": len(chunks) if use_full_doc else len(
            _select_chunks_for_stage(chunks, "entity_extraction", llm_cfg, document.doc_type)
        ),
        "extraction_mode": "full_doc" if use_full_doc else "chunked",
        "mention_count": success_count,
        "error_count": len(errors),
        "run_status": run.status.value,
    }


def _extract_obligations_impl(
    db: Session,
    document: Document,
    run: ExtractionRun,
    llm_cfg: dict,
    *,
    force_all_chunks: bool = False,
) -> dict[str, object]:
    all_chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
        .all()
    )
    extraction_cfg = settings.raw.get("extraction", {})
    max_non_agreement_ratio_before_bypass, _ = _section_filter_settings(extraction_cfg)
    chunks, section_filter_stats, section_filter_bypassed, chunk_source = _select_chunks_with_section_filter_guardrails(
        all_chunks=all_chunks,
        max_non_agreement_ratio_before_bypass=max_non_agreement_ratio_before_bypass,
        force_all_chunks=force_all_chunks,
        stage_name="obligation_extraction",
    )
    entities = db.query(Entity).all()
    use_full_doc = _should_use_full_doc(chunks, extraction_cfg)

    if use_full_doc:
        try:
            model_used, outputs, errors = _run_full_doc_call(
                chunks=chunks, stage_name="obligation_extraction",
                document=document, llm_cfg=llm_cfg,
            )
        except Exception:
            logger.warning("Full-doc extraction failed for obligations, falling back to chunked")
            use_full_doc = False

    if not use_full_doc:
        def _build(model: str, chunk: Chunk) -> str:
            return _build_extraction_prompt("obligation_extraction", chunk, document)

        def _build_group(model: str, group: list[Chunk]) -> str:
            return _build_grouped_extraction_prompt("obligation_extraction", group, document)

        model_used, outputs, errors = _run_grouped_chunk_calls(
            chunks=chunks,
            stage_name="obligation_extraction",
            doc_type=document.doc_type,
            llm_cfg=llm_cfg,
            build_prompt=_build,
            build_group_prompt=_build_group,
        )
    aliases = _get_obligation_aliases(document.doc_type)

    parsed_candidates: list[dict[str, object]] = []
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

            parsed_candidates.append(
                {
                    "obligation_type": obligation_type,
                    "obligation_text": obligation_text,
                    "modality": modality,
                    "responsible_entity_id": responsible_entity_id,
                    "due_kind": due_kind,
                    "due_date": due_date,
                    "due_rule": due_rule,
                    "severity": severity,
                }
            )

    deduped_candidates, removed_count = _dedupe_candidates(
        parsed_candidates,
        text_key="obligation_text",
        score_fn=_obligation_candidate_score,
    )

    success_count = 0
    for candidate in deduped_candidates:
        record = Obligation(
            id=uuid.uuid4(),
            document_id=document.id,
            obligation_type=candidate["obligation_type"],
            obligation_text=str(candidate["obligation_text"]),
            modality=candidate["modality"],
            responsible_entity_id=candidate["responsible_entity_id"],
            due_kind=candidate["due_kind"],
            due_date=candidate["due_date"],
            due_rule=candidate["due_rule"],
            trigger_date=None,
            severity=candidate["severity"],
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
        "selected_chunk_count": len(chunks) if use_full_doc else len(
            _select_chunks_for_stage(chunks, "obligation_extraction", llm_cfg, document.doc_type)
        ),
        "extraction_mode": "full_doc" if use_full_doc else "chunked",
        "raw_obligation_count": len(parsed_candidates),
        "deduplicated_obligation_count": len(deduped_candidates),
        "dedup_removed_count": removed_count,
        "obligation_count": success_count,
        "error_count": len(errors),
        "run_status": run.status.value,
        "section_filter_stats": section_filter_stats,
        "section_filter_bypassed": section_filter_bypassed,
        "chunk_source": chunk_source,
    }


def _extract_risks_impl(
    db: Session,
    document: Document,
    run: ExtractionRun,
    llm_cfg: dict,
    *,
    force_all_chunks: bool = False,
) -> dict[str, object]:
    all_chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
        .all()
    )

    extraction_cfg = settings.raw.get("extraction", {})
    max_non_agreement_ratio_before_bypass, _ = _section_filter_settings(extraction_cfg)
    chunks, section_filter_stats, section_filter_bypassed, chunk_source = _select_chunks_with_section_filter_guardrails(
        all_chunks=all_chunks,
        max_non_agreement_ratio_before_bypass=max_non_agreement_ratio_before_bypass,
        force_all_chunks=force_all_chunks,
        stage_name="risk_extraction",
    )
    use_full_doc = _should_use_full_doc(chunks, extraction_cfg)

    if use_full_doc:
        try:
            model_used, outputs, errors = _run_full_doc_call(
                chunks=chunks, stage_name="risk_extraction",
                document=document, llm_cfg=llm_cfg,
            )
        except Exception:
            logger.warning("Full-doc extraction failed for risks, falling back to chunked")
            use_full_doc = False

    if not use_full_doc:
        def _build(model: str, chunk: Chunk) -> str:
            return _build_extraction_prompt("risk_extraction", chunk, document)

        def _build_group(model: str, group: list[Chunk]) -> str:
            return _build_grouped_extraction_prompt("risk_extraction", group, document)

        model_used, outputs, errors = _run_grouped_chunk_calls(
            chunks=chunks,
            stage_name="risk_extraction",
            doc_type=document.doc_type,
        llm_cfg=llm_cfg,
        build_prompt=_build,
        build_group_prompt=_build_group,
    )

    parsed_candidates: list[dict[str, object]] = []
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

            parsed_candidates.append(
                {
                    "risk_type": risk_type,
                    "risk_text": risk_text,
                    "severity": severity,
                }
            )

    deduped_candidates, removed_count = _dedupe_candidates(
        parsed_candidates,
        text_key="risk_text",
        score_fn=_risk_candidate_score,
    )

    success_count = 0
    for candidate in deduped_candidates:
        record = Risk(
            id=uuid.uuid4(),
            document_id=document.id,
            risk_type=candidate["risk_type"],
            risk_text=str(candidate["risk_text"]),
            severity=candidate["severity"],
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
        "selected_chunk_count": len(chunks) if use_full_doc else len(
            _select_chunks_for_stage(chunks, "risk_extraction", llm_cfg, document.doc_type)
        ),
        "extraction_mode": "full_doc" if use_full_doc else "chunked",
        "raw_risk_count": len(parsed_candidates),
        "deduplicated_risk_count": len(deduped_candidates),
        "dedup_removed_count": removed_count,
        "risk_count": success_count,
        "error_count": len(errors),
        "run_status": run.status.value,
        "section_filter_stats": section_filter_stats,
        "section_filter_bypassed": section_filter_bypassed,
        "chunk_source": chunk_source,
    }


def _extract_classified_impl(
    db: Session,
    document: Document,
    ob_run: ExtractionRun,
    ri_run: ExtractionRun,
    llm_cfg: dict,
    *,
    force_all_chunks: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    """Combined obligation+risk extraction via clause classification."""
    all_chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document.id)
        .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
        .all()
    )
    extraction_cfg = settings.raw.get("extraction", {})
    max_non_agreement_ratio_before_bypass, _ = _section_filter_settings(extraction_cfg)
    chunks, section_filter_stats, section_filter_bypassed, chunk_source = _select_chunks_with_section_filter_guardrails(
        all_chunks=all_chunks,
        max_non_agreement_ratio_before_bypass=max_non_agreement_ratio_before_bypass,
        force_all_chunks=force_all_chunks,
        stage_name="classify_extraction",
    )
    entities = db.query(Entity).all()
    aliases = _get_obligation_aliases(document.doc_type)

    model_used, response, errors = _run_full_doc_classify_call(
        chunks=chunks, document=document, llm_cfg=llm_cfg,
    )

    # Build synthetic outputs for _finish_run
    outputs = [{"chunk_id": str(chunks[0].id) if chunks else "none", "model": model_used, "response": response}]

    # --- Parse obligations ---
    ob_candidates: list[dict[str, object]] = []
    for entry in response.get("obligations") or []:
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

        ob_candidates.append(
            {
                "obligation_type": obligation_type,
                "obligation_text": obligation_text,
                "modality": modality,
                "responsible_entity_id": responsible_entity_id,
                "due_kind": due_kind,
                "due_date": due_date,
                "due_rule": due_rule,
                "severity": severity,
            }
        )

    # --- Parse risks ---
    ri_candidates: list[dict[str, object]] = []
    for entry in response.get("risks") or []:
        if not isinstance(entry, dict):
            continue
        risk_text = str(entry.get("quote", "")).strip() or str(entry.get("risk_text", "")).strip()
        if not risk_text:
            continue

        risk_type = _coerce_enum(entry.get("risk_type"), RiskType, RiskType.unknown_risk)
        severity = _coerce_enum(entry.get("severity"), Severity, Severity.medium)

        ri_candidates.append(
            {
                "risk_type": risk_type,
                "risk_text": risk_text,
                "severity": severity,
            }
        )

    # --- Dedupe ---
    deduped_obs, ob_removed = _dedupe_candidates(ob_candidates, text_key="obligation_text", score_fn=_obligation_candidate_score)
    deduped_risks, ri_removed = _dedupe_candidates(ri_candidates, text_key="risk_text", score_fn=_risk_candidate_score)

    # --- Persist obligations ---
    ob_count = 0
    for candidate in deduped_obs:
        record = Obligation(
            id=uuid.uuid4(),
            document_id=document.id,
            obligation_type=candidate["obligation_type"],
            obligation_text=str(candidate["obligation_text"]),
            modality=candidate["modality"],
            responsible_entity_id=candidate["responsible_entity_id"],
            due_kind=candidate["due_kind"],
            due_date=candidate["due_date"],
            due_rule=candidate["due_rule"],
            trigger_date=None,
            severity=candidate["severity"],
            status=ReviewStatus.needs_review,
            system_confidence=0,
            reviewer_confidence=None,
            has_external_reference=False,
            contradiction_flag=False,
            extraction_run_id=ob_run.id,
        )
        db.add(record)
        ob_count += 1

    # --- Persist risks ---
    ri_count = 0
    for candidate in deduped_risks:
        record = Risk(
            id=uuid.uuid4(),
            document_id=document.id,
            risk_type=candidate["risk_type"],
            risk_text=str(candidate["risk_text"]),
            severity=candidate["severity"],
            status=ReviewStatus.needs_review,
            system_confidence=0,
            reviewer_confidence=None,
            has_external_reference=False,
            contradiction_flag=False,
            extraction_run_id=ri_run.id,
        )
        db.add(record)
        ri_count += 1
    db.commit()

    # Finish both runs
    _finish_run(db=db, run=ob_run, model_used=model_used, outputs=outputs, errors=errors, success_count=ob_count)
    _finish_run(db=db, run=ri_run, model_used=model_used, outputs=outputs, errors=errors, success_count=ri_count)

    ob_summary = {
        "run_id": str(ob_run.id),
        "model_used": model_used,
        "extraction_mode": "classify",
        "raw_obligation_count": len(ob_candidates),
        "deduplicated_obligation_count": len(deduped_obs),
        "dedup_removed_count": ob_removed,
        "obligation_count": ob_count,
        "error_count": len(errors),
        "run_status": ob_run.status.value,
        "section_filter_stats": section_filter_stats,
        "section_filter_bypassed": section_filter_bypassed,
        "chunk_source": chunk_source,
    }
    ri_summary = {
        "run_id": str(ri_run.id),
        "model_used": model_used,
        "extraction_mode": "classify",
        "raw_risk_count": len(ri_candidates),
        "deduplicated_risk_count": len(deduped_risks),
        "dedup_removed_count": ri_removed,
        "risk_count": ri_count,
        "error_count": len(errors),
        "run_status": ri_run.status.value,
        "section_filter_stats": section_filter_stats,
        "section_filter_bypassed": section_filter_bypassed,
        "chunk_source": chunk_source,
    }
    return ob_summary, ri_summary


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


def extract_obligations_and_risks(document_id: str) -> dict[str, object]:
    """Combined obligation+risk extraction using clause classification (full-doc)
    or separate search prompts (chunked fallback)."""
    update_parse_status(str(document_id), ParseStatus.extraction)

    db: Session = SessionLocal()
    try:
        doc_id = _to_uuid(document_id)
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            return {"document_id": str(document_id), "status": "not_found"}
        if document.parse_status == ParseStatus.failed:
            return {"document_id": str(document.id), "status": "skipped", "reason": "parse_failed"}

        llm_cfg = settings.raw.get("llm", {})
        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == document.id)
            .order_by(Chunk.page_number.asc(), Chunk.char_start.asc())
            .all()
        )
        extraction_cfg = settings.raw.get("extraction", {})
        use_classify = _should_use_full_doc(chunks, extraction_cfg)

        _, retry_all_chunks_on_zero_results = _section_filter_settings(extraction_cfg)

        def _run_once(*, prefer_classify: bool, force_all_chunks: bool) -> tuple[str, dict[str, object], dict[str, object]]:
            if prefer_classify:
                try:
                    ob_run = _start_run(
                        db=db, document=document,
                        stage=ExtractionStage.obligation_extraction,
                        prompt_name="classify_obligations_default", llm_cfg=llm_cfg,
                    )
                    ri_run = _start_run(
                        db=db, document=document,
                        stage=ExtractionStage.risk_extraction,
                        prompt_name="classify_risks_default", llm_cfg=llm_cfg,
                    )
                    ob_summary_local, ri_summary_local = _extract_classified_impl(
                        db,
                        document,
                        ob_run,
                        ri_run,
                        llm_cfg,
                        force_all_chunks=force_all_chunks,
                    )
                    return "classify", ob_summary_local, ri_summary_local
                except Exception:
                    logger.warning("Classify extraction failed, falling back to chunked mode")

            ob_run = _start_run(
                db=db, document=document,
                stage=ExtractionStage.obligation_extraction,
                prompt_name="extract_obligations_default", llm_cfg=llm_cfg,
            )
            ob_summary_local = _extract_obligations_impl(
                db,
                document,
                ob_run,
                llm_cfg,
                force_all_chunks=force_all_chunks,
            )

            ri_run = _start_run(
                db=db, document=document,
                stage=ExtractionStage.risk_extraction,
                prompt_name="extract_risks_default", llm_cfg=llm_cfg,
            )
            ri_summary_local = _extract_risks_impl(
                db,
                document,
                ri_run,
                llm_cfg,
                force_all_chunks=force_all_chunks,
            )
            return "chunked", ob_summary_local, ri_summary_local

        mode, ob_summary, ri_summary = _run_once(prefer_classify=use_classify, force_all_chunks=False)
        initial_counts = {
            "obligations": int(ob_summary.get("obligation_count", 0) or 0),
            "risks": int(ri_summary.get("risk_count", 0) or 0),
        }

        zero_result_retry_attempted = False
        zero_result_retry_succeeded = False

        should_retry_with_all_chunks = (
            retry_all_chunks_on_zero_results
            and initial_counts["obligations"] == 0
            and initial_counts["risks"] == 0
            and ob_summary.get("chunk_source") == "filtered"
            and ri_summary.get("chunk_source") == "filtered"
        )

        if should_retry_with_all_chunks:
            zero_result_retry_attempted = True
            logger.warning(
                "Zero-result extraction after section-filtered pass for %s; retrying once with all chunks",
                str(document.id)[:8],
            )
            mode, ob_summary, ri_summary = _run_once(
                prefer_classify=(mode == "classify"),
                force_all_chunks=True,
            )
            final_retry_counts = {
                "obligations": int(ob_summary.get("obligation_count", 0) or 0),
                "risks": int(ri_summary.get("risk_count", 0) or 0),
            }
            zero_result_retry_succeeded = (
                final_retry_counts["obligations"] > 0 or final_retry_counts["risks"] > 0
            )

        final_counts = {
            "obligations": int(ob_summary.get("obligation_count", 0) or 0),
            "risks": int(ri_summary.get("risk_count", 0) or 0),
        }

        return {
            "document_id": str(document.id),
            "status": "ok" if ob_summary.get("error_count", 0) == 0 and ri_summary.get("error_count", 0) == 0 else "partial",
            "mode": mode,
            "obligations": ob_summary,
            "risks": ri_summary,
            "zero_result_retry_attempted": zero_result_retry_attempted,
            "zero_result_retry_succeeded": zero_result_retry_succeeded,
            "initial_counts": initial_counts,
            "final_counts": final_counts,
        }
    finally:
        db.close()
