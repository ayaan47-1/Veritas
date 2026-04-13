# Adversarial Critic Stage — Design Spec

## Problem

Full-doc extraction achieves F1 of 50.0% (obligations) and 58.7% (risks). The bottlenecks differ by type:
- **Obligations:** 66 false positives out of 109 extracted (precision 39.4%). Pipeline over-extracts from statutory summaries and boilerplate.
- **Risks:** 19 false negatives out of 41 GT (recall 53.7%). Pipeline misses genuine risk clauses.

A single-pass extraction can't fix both — tighter prompts improve precision but hurt recall, and vice versa. An adversarial critic (second LLM pass) validates existing extractions AND detects missed items.

## Design

### Stage Placement

```
Extract (6-8) → Verify (9) → CRITIC (9a) → Score (10) → Rescore (10b)
```

The critic runs AFTER verify (needs grounded quotes) and BEFORE score (cleans the dataset before scoring). New `ParseStatus` value: `critic_review`.

### Critic Responsibilities

**Validate (reduce FPs):** For each extracted obligation/risk, review against source text:
- Is this a genuine contractual obligation/risk from THIS agreement?
- Or is it from an attached statutory summary, tenant rights disclosure, general statement of law, or boilerplate?
- Is the classification correct (obligation vs risk, type, severity)?

**Detect (reduce FNs):** Given the full document and existing extractions:
- Are there obligations/risks in the document that were not extracted?
- Return new items in the same format (quote, type, severity).

### New Model Fields

Add to both `Obligation` and `Risk` models (nullable, non-destructive):

```python
critic_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
critic_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
critic_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Check constraint: `critic_confidence` between 0 and 100.

### Auto-Rejection Rules

- `critic_valid = False` AND `critic_confidence >= 70` → `status = rejected`
- `critic_valid = False` AND `critic_confidence < 70` → stays `needs_review` (human decides)
- `critic_valid = True` → no status change

### New Items from Critic

Newly detected items follow the existing pipeline:
1. Critic returns new items as JSON (same schema as extraction output)
2. Items are persisted as Obligation/Risk records (with `critic_valid = True`, linked to a new ExtractionRun with stage `critic_detection`)
3. Items go through `_verify_obligations` / `_verify_risks` to create evidence records
4. Items that fail verify get `status = rejected` (same as extraction items)

### LLM Prompt Structure

Single prompt per batch (follow rescore's batching pattern, configurable `max_items_per_call`):

```
You are a legal document auditor. Your task is to review extracted obligations
and risks for accuracy, and identify any that were missed.

Document type: {doc_type}

PART 1 — VALIDATE EXISTING ITEMS
Review each item below. For each, determine:
- Is this a genuine obligation/risk from THIS specific agreement?
- Is it correctly classified (type, severity)?
- Should it be kept or rejected?

Do NOT reject items just because they are standard/common — only reject if they
are NOT actually present in the agreement text or are from an attached statutory
summary rather than the agreement itself.

Items to validate:
{numbered list of items with quotes and metadata}

PART 2 — DETECT MISSED ITEMS
Given the document text below, identify any obligations or risks that were
NOT captured in the items above. Only include items that clearly impose a duty
or expose a party to liability.

Document text:
{full document text}

Return JSON:
{
  "validations": [
    {"id": "...", "valid": true|false, "confidence": 0-100, "reasoning": "..."}
  ],
  "new_obligations": [
    {"quote": "...", "obligation_type": "...", "modality": "...", "severity": "...",
     "due_date": null, "due_rule": null, "responsible_party": null}
  ],
  "new_risks": [
    {"quote": "...", "risk_type": "...", "severity": "..."}
  ]
}
```

### Config

Add to `backend/config.yaml`:

```yaml
critic:
  enabled: true
  model: "claude-sonnet-4-6"
  max_items_per_call: 30
  auto_reject_threshold: 70    # critic_confidence >= this → auto-reject invalid items
```

### Implementation Files

| File | Change |
|------|--------|
| `backend/app/models/enums.py` | Add `critic_review` to ParseStatus |
| `backend/app/models/obligation.py` | Add `critic_valid`, `critic_confidence`, `critic_reasoning` |
| `backend/app/models/risk.py` | Add same 3 fields |
| `backend/app/worker/tasks/critic.py` | New file — `criticize_extractions(document_id)` |
| `backend/app/worker/pipeline.py` | Add stage 9a step |
| `backend/config.yaml` | Add `critic` section |
| `backend/tools/rerun_extraction.py` | Add critic stage to rerun sequence |
| `backend/alembic/versions/...` | Migration for new columns |
| `backend/tests/test_critic_task.py` | New test file |

### Expected Impact

Based on L-MARS benchmarks (98% vs 86-89% single-pass) and the current numbers:
- Obligations precision: 39.4% → ~55-65% (critic filters FPs from statutory summaries)
- Risks recall: 53.7% → ~65-75% (critic catches missed risk clauses)
- Combined F1 target: 60-70% for both types

### Cost

~$0.10-0.15 per document additional LLM cost (one Sonnet call per batch of 30 items + full doc text). Total pipeline cost rises from ~$0.30 to ~$0.45 per document.
