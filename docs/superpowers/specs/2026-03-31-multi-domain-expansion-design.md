# Design & Implementation Plan: Multi-Domain Document Expansion

**Date:** 2026-03-31
**Project:** VeritasLayer
**Branch:** master
**Implementer:** Codex

---

## Context

VeritasLayer currently recognizes obligations and risks in construction-focused documents (contracts, RFIs, change orders, leases, inspection reports, invoices). This expansion adds two new domains:

- **Real estate additions:** purchase agreements, title commitments, HOA documents, disclosure reports
- **Financial (new domain):** insurance policies, loan agreements, deeds of trust

The output schema stays unchanged (same obligation/risk fields). The goal is better *recognition* — domain-appropriate vocabulary in extraction prompts, correct keyword scoring, and domain-aware alignment checks — not new structured fields.

**Chosen architecture: Domain Profile (config-driven).** A `domain` layer (`construction`, `real_estate`, `financial`, `general`) sits above doc types. Each domain profile in `config.yaml` carries classification heuristics, MMR stage keywords, obligation type aliases, vocab preambles, and doc-type alignment mappings. Code reads from config; adding a future domain requires only config changes.

---

## Critical Files

| File | Change |
|---|---|
| `backend/config.yaml` | Add full `domains:` block |
| `backend/app/models/enums.py` | Add 7 new `DocumentType` values |
| `backend/app/models/document.py` | Add `domain: Mapped[str | None]` column |
| `backend/alembic/versions/<new>.py` | Migration: new enum values + domain column |
| `backend/app/worker/tasks/classify.py` | Config-driven heuristics, prompt, domain assignment |
| `backend/app/worker/tasks/extract.py` | Config-driven keywords, aliases, vocab preamble injection |
| `backend/app/worker/tasks/score.py` | Config-driven `_doc_type_aligned` |
| `backend/app/worker/tasks/rescore.py` | Domain-aware persona in rescore prompt |
| `backend/app/routers/assets.py` | Add `domain` to `_serialize_document` |
| `backend/app/routers/obligations.py` | Include `document_domain` on obligation rows |
| `backend/app/schemas/documents.py` | Add `domain: Optional[str] = None` to `DocumentOut` |
| `frontend/src/lib/types.ts` | Add `domain: string \| null` to `DocumentSummary`, `DocumentDetail`, `Obligation` |
| `frontend/src/app/assets/[id]/documents/page.tsx` | Expand `DOC_TYPES`, add Domain column |
| `frontend/src/app/documents/[id]/page.tsx` | Add domain cell to metadata grid |
| `frontend/src/app/obligations/ObligationsClientPage.tsx` | Add domain filter |
| `backend/tests/test_classification_task.py` | New + updated tests |
| `backend/tests/test_pipeline_tasks.py` | New extraction tests |
| `backend/tests/test_score_task.py` | New + updated scoring tests |

---

## TDD Mandate

**Codex MUST follow red-green order for every phase below.**
For each step:
1. Write the test(s) first
2. Run `python3 -m pytest <test_file> -q` — confirm it **fails** (red)
3. Write the minimum implementation to make it pass
4. Run pytest again — confirm it **passes** (green)
5. Run `python3 -m compileall backend/app backend/alembic backend/tools -q` after each phase

The overall test suite baseline is **83 tests, all passing**. Every phase must leave the suite green before proceeding.

---

## Phase 1 — Enums (Foundation)

### Test first (red)
```python
# backend/tests/test_new_doc_types.py
from backend.app.models.enums import DocumentType

def test_new_real_estate_doc_types_exist():
    assert DocumentType.purchase_agreement
    assert DocumentType.title_commitment
    assert DocumentType.hoa_document
    assert DocumentType.disclosure_report

def test_new_financial_doc_types_exist():
    assert DocumentType.insurance_policy
    assert DocumentType.loan_agreement
    assert DocumentType.deed_of_trust
```

Run → confirm AttributeError (red).

### Implement
Add to `DocumentType` in `backend/app/models/enums.py`:
```python
# real_estate additions
purchase_agreement = "purchase_agreement"
title_commitment = "title_commitment"
hoa_document = "hoa_document"
disclosure_report = "disclosure_report"
# financial
insurance_policy = "insurance_policy"
loan_agreement = "loan_agreement"
deed_of_trust = "deed_of_trust"
```

Run → green. Run full suite → still 83+ passing.

---

## Phase 2 — DB Migration

No TDD for migration itself (DDL). Write and run:

```python
# backend/alembic/versions/<revision_id>_add_domain_column_and_new_doc_types.py
# down_revision = "f3c7beac04b9"

def upgrade():
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'purchase_agreement'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'title_commitment'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'hoa_document'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'disclosure_report'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'insurance_policy'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'loan_agreement'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'deed_of_trust'")
    op.add_column("documents", sa.Column("domain", sa.String(), nullable=True))

def downgrade():
    op.drop_column("documents", "domain")
    # Note: PostgreSQL enum values cannot be removed; document in migration comment
```

Add to `Document` model (`backend/app/models/document.py`):
```python
domain: Mapped[str | None] = mapped_column(String, nullable=True)
```

Add to `DocumentOut` (`backend/app/schemas/documents.py`):
```python
domain: Optional[str] = None
```

Run `python3 -m alembic -c backend/alembic.ini upgrade head`. Verify existing rows have `domain = NULL`.

---

## Phase 3 — Config (Domain Profiles)

Add the full `domains:` block to `backend/config.yaml`. This is the single source of truth for all domain logic.

```yaml
domains:
  construction:
    doc_types:
      - contract
      - rfi
      - change_order
      - invoice
    heuristics:
      contract:
        - agree
        - party
        - parties
        - shall
        - obligation
      rfi:
        - request for information
        - clarification
        - rfi
      change_order:
        - change order
        - modification
        - amendment
      invoice:
        - invoice
        - amount
        - total
        - usd
    stage_keywords:
      obligation_extraction:
        - shall
        - must
        - required
        - within
        - deadline
        - deliver
        - submit
        - payment
        - comply
      risk_extraction:
        - penalty
        - damages
        - liquidated
        - indemnif
        - bond
        - insurance
        - risk
        - breach
        - liable
        - delay
        - default
        - terminate
        - non-compliance
      entity_extraction:
        - llc
        - inc
        - company
        - contractor
        - owner
        - party
        - city
        - county
        - address
    obligation_aliases:
      delivery: submission
      maintenance: inspection
      reporting: compliance
    vocab_preambles:
      obligation_extraction: >
        This is a construction industry document. Focus on contractual duties,
        scope obligations, schedule milestones, submittal requirements, and
        payment obligations typical of AIA/ConsensusDocs forms.
      risk_extraction: >
        This is a construction industry document. Focus on liquidated damages,
        retainage clauses, indemnification, insurance requirements, delay
        penalties, and lien exposure.
    doc_type_aligned:
      invoice:
        - payment
      contract:
        - compliance
        - submission
        - payment
        - notification
      rfi:
        - submission
        - notification
      change_order:
        - payment
        - submission

  real_estate:
    doc_types:
      - lease
      - inspection_report
      - purchase_agreement
      - title_commitment
      - hoa_document
      - disclosure_report
    heuristics:
      lease:
        - tenant
        - landlord
        - rent
        - lease
        - lessee
        - lessor
        - tenancy
      inspection_report:
        - inspect
        - examin
        - assess
        - finding
      purchase_agreement:
        - purchase price
        - buyer
        - seller
        - closing
        - contingency
        - escrow
      title_commitment:
        - title insurance
        - commitment
        - title defect
        - encumbrance
        - schedule b
      hoa_document:
        - homeowner
        - association
        - assessment
        - cc&r
        - bylaw
        - common area
      disclosure_report:
        - disclosure
        - lead
        - mold
        - environmental
        - seller disclosure
    stage_keywords:
      obligation_extraction:
        - shall
        - must
        - required
        - tenant
        - landlord
        - buyer
        - seller
        - closing
        - assessment
        - disclose
      risk_extraction:
        - defect
        - encumbrance
        - lien
        - default
        - breach
        - eviction
        - easement
        - hazard
        - contamination
      entity_extraction:
        - buyer
        - seller
        - tenant
        - landlord
        - association
        - title company
        - escrow
    obligation_aliases:
      delivery: submission
      maintenance: inspection
      reporting: compliance
    vocab_preambles:
      obligation_extraction: >
        This is a real estate document. Focus on closing conditions, inspection
        contingencies, HOA assessments, disclosure requirements, rent payment
        terms, and title clearance obligations.
      risk_extraction: >
        This is a real estate document. Focus on title defects, encumbrances,
        environmental hazards, HOA violations, lease default triggers, and
        disclosure liability.
    doc_type_aligned:
      lease:
        - payment
        - compliance
        - notification
      inspection_report:
        - inspection
        - compliance
      purchase_agreement:
        - payment
        - submission
        - compliance
      title_commitment:
        - compliance
        - submission
      hoa_document:
        - payment
        - compliance
      disclosure_report:
        - compliance
        - notification

  financial:
    doc_types:
      - insurance_policy
      - loan_agreement
      - deed_of_trust
    heuristics:
      insurance_policy:
        - insured
        - insurer
        - premium
        - coverage
        - deductible
        - policyholder
        - claim
      loan_agreement:
        - borrower
        - lender
        - promissory
        - principal
        - interest rate
        - repayment
        - maturity
      deed_of_trust:
        - trustor
        - trustee
        - beneficiary
        - deed of trust
        - mortgage
        - security instrument
        - encumber
    stage_keywords:
      obligation_extraction:
        - shall
        - must
        - required
        - premium
        - repayment
        - payment
        - insured
        - borrower
        - lender
        - covenants
        - maintain
        - disclose
      risk_extraction:
        - default
        - foreclosure
        - acceleration
        - claim denial
        - exclusion
        - subrogation
        - lapse
        - non-payment
        - breach
        - penalty
        - liability
      entity_extraction:
        - borrower
        - lender
        - insurer
        - insured
        - trustee
        - beneficiary
        - mortgagee
    obligation_aliases:
      delivery: submission
      maintenance: inspection
      reporting: compliance
    vocab_preambles:
      obligation_extraction: >
        This is a financial/insurance document. Focus on premium payment
        obligations, loan repayment schedules, covenant compliance requirements,
        insurance maintenance duties, and disclosure obligations to the lender
        or insurer.
      risk_extraction: >
        This is a financial/insurance document. Focus on default and
        acceleration clauses, foreclosure triggers, claim exclusions, coverage
        lapses, subrogation rights, and personal liability exposure.
    doc_type_aligned:
      insurance_policy:
        - payment
        - compliance
        - notification
      loan_agreement:
        - payment
        - compliance
        - submission
      deed_of_trust:
        - payment
        - compliance

  general:
    doc_types:
      - unknown
    heuristics:
      unknown: []
    stage_keywords:
      obligation_extraction:
        - shall
        - must
        - required
        - within
        - deadline
      risk_extraction:
        - penalty
        - damages
        - risk
        - breach
        - default
      entity_extraction:
        - company
        - party
        - address
    obligation_aliases:
      delivery: submission
      maintenance: inspection
      reporting: compliance
    vocab_preambles:
      obligation_extraction: >
        Extract all obligations, duties, and requirements from this document.
      risk_extraction: >
        Extract all risks, liabilities, and penalty clauses from this document.
    doc_type_aligned: {}
```

No tests for raw YAML — tested implicitly by later phases.

---

## Phase 4 — Classification (classify.py)

### Tests first (red)

Add to `backend/tests/test_classification_task.py`:

```python
# Helper: minimal domains config for testing
MINIMAL_DOMAINS = {
    "financial": {
        "doc_types": ["insurance_policy", "loan_agreement", "deed_of_trust"],
        "heuristics": {
            "insurance_policy": ["insured", "premium", "coverage"],
            "loan_agreement": ["borrower", "lender", "promissory"],
            "deed_of_trust": ["trustor", "deed of trust", "mortgage"],
        },
    },
    "real_estate": {
        "doc_types": ["purchase_agreement"],
        "heuristics": {
            "purchase_agreement": ["purchase price", "buyer", "closing"],
        },
    },
    "general": {
        "doc_types": ["unknown"],
        "heuristics": {"unknown": []},
    },
}

def test_heuristics_match_loads_from_config(monkeypatch):
    monkeypatch.setattr(classify_task, "settings", type("S", (), {"raw": {"domains": MINIMAL_DOMAINS}})())
    assert classify_task._heuristics_match(DocumentType.insurance_policy, "the insured must pay the premium")
    assert not classify_task._heuristics_match(DocumentType.insurance_policy, "this is a lease agreement")

def test_heuristics_match_unknown_always_true(monkeypatch):
    monkeypatch.setattr(classify_task, "settings", type("S", (), {"raw": {"domains": MINIMAL_DOMAINS}})())
    assert classify_task._heuristics_match(DocumentType.unknown, "any text at all")

def test_build_prompt_includes_all_configured_doc_types(monkeypatch):
    monkeypatch.setattr(classify_task, "settings", type("S", (), {"raw": {"domains": MINIMAL_DOMAINS}})())
    prompt = classify_task._build_prompt(["sample page text"])
    assert "insurance_policy" in prompt
    assert "loan_agreement" in prompt
    assert "purchase_agreement" in prompt

def test_domain_derived_and_stored_after_classify(monkeypatch):
    # Full classify call: LLM returns deed_of_trust, domain should be set to "financial"
    fake_doc = FakeDocument(doc_type=None, domain=None)
    monkeypatch.setattr(classify_task, "settings", type("S", (), {"raw": {"domains": MINIMAL_DOMAINS, "classification": {"sample_pages": 3}, "llm": {"max_retries": 1, "retry_backoff_base": 0}}})())
    monkeypatch.setattr(classify_task, "llm_completion", lambda *a, **kw: '{"doc_type":"deed_of_trust","confidence":0.9,"explanation":"test"}')
    monkeypatch.setattr(classify_task, "SessionLocal", lambda: FakeSession(fake_doc))
    classify_task.classify_document({"document_id": str(fake_doc.id)})
    assert fake_doc.domain == "financial"

def test_unknown_doc_type_maps_to_general_domain(monkeypatch):
    fake_doc = FakeDocument(doc_type=None, domain=None)
    monkeypatch.setattr(classify_task, "settings", type("S", (), {"raw": {"domains": MINIMAL_DOMAINS, "classification": {"sample_pages": 3}, "llm": {"max_retries": 1, "retry_backoff_base": 0}}})())
    monkeypatch.setattr(classify_task, "llm_completion", lambda *a, **kw: '{"doc_type":"unknown","confidence":0.5,"explanation":"test"}')
    monkeypatch.setattr(classify_task, "SessionLocal", lambda: FakeSession(fake_doc))
    classify_task.classify_document({"document_id": str(fake_doc.id)})
    assert fake_doc.domain == "general"
```

Run → red (functions don't exist yet or behave differently).

### Implement

In `classify.py`, add these helpers and update `classify_document`:

```python
def _domain_for_doc_type(doc_type: DocumentType) -> str:
    domain_cfg = settings.raw.get("domains", {})
    for domain_name, domain_data in domain_cfg.items():
        if doc_type.value in domain_data.get("doc_types", []):
            return domain_name
    return "general"

def _heuristics_match(doc_type: DocumentType, text_blob: str) -> bool:
    text = text_blob.lower()
    if doc_type == DocumentType.unknown:
        return True
    # Special case: invoice currency regex (cannot be expressed as a keyword list)
    if doc_type == DocumentType.invoice:
        import re
        if re.search(r"\$\s?\d", text):
            return True
    domain_cfg = settings.raw.get("domains", {})
    for domain_data in domain_cfg.values():
        tokens = domain_data.get("heuristics", {}).get(doc_type.value, [])
        if tokens and any(token in text for token in tokens):
            return True
    return False

def _build_prompt(sample_pages: list[str]) -> str:
    domain_cfg = settings.raw.get("domains", {})
    all_types: list[str] = []
    for domain_data in domain_cfg.values():
        all_types.extend(domain_data.get("doc_types", []))
    if not all_types:
        all_types = [dt.value for dt in DocumentType]
    type_list = ", ".join(sorted(set(all_types)))
    joined = "\n\n".join(sample_pages)[:12000]
    return (
        f"Classify the document type as one of: {type_list}. "
        'Return compact JSON: {"doc_type":"...","confidence":0.0,"explanation":"..."}.\n\n'
        f"Document excerpts:\n{joined}"
    )
```

In `classify_document`, immediately after setting `document.doc_type`:
```python
document.domain = _domain_for_doc_type(detected_type)
```

Also update existing tests that monkeypatch `settings.raw` to include a `domains` block (even a minimal one) so `_build_prompt` still gets a type list.

Run → green. Run full suite → all passing.

---

## Phase 5 — Extraction (extract.py)

### Tests first (red)

Add to `backend/tests/test_pipeline_tasks.py` (or a new `test_extraction_domain.py`):

```python
FINANCIAL_DOMAINS = {
    "financial": {
        "doc_types": ["insurance_policy", "loan_agreement", "deed_of_trust"],
        "stage_keywords": {
            "obligation_extraction": ["premium", "repayment", "borrower", "lender"],
            "risk_extraction": ["default", "foreclosure", "exclusion", "lapse"],
            "entity_extraction": ["borrower", "lender", "insurer", "trustee"],
        },
        "obligation_aliases": {"delivery": "submission", "maintenance": "inspection"},
        "vocab_preambles": {
            "obligation_extraction": "This is a financial/insurance document.",
            "risk_extraction": "This is a financial/insurance document.",
        },
    },
    "general": {
        "doc_types": ["unknown"],
        "stage_keywords": {
            "obligation_extraction": ["shall", "must"],
            "risk_extraction": ["penalty", "breach"],
            "entity_extraction": ["party"],
        },
        "obligation_aliases": {},
        "vocab_preambles": {},
    },
}

def test_get_stage_keywords_returns_domain_keywords(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", type("S", (), {"raw": {"domains": FINANCIAL_DOMAINS}})())
    kw = extract_task._get_stage_keywords("obligation_extraction", DocumentType.loan_agreement)
    assert "premium" in kw
    assert "borrower" in kw

def test_get_stage_keywords_falls_back_to_general(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", type("S", (), {"raw": {"domains": FINANCIAL_DOMAINS}})())
    kw = extract_task._get_stage_keywords("obligation_extraction", DocumentType.unknown)
    assert "shall" in kw

def test_get_obligation_aliases_returns_domain_aliases(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", type("S", (), {"raw": {"domains": FINANCIAL_DOMAINS}})())
    aliases = extract_task._get_obligation_aliases(DocumentType.insurance_policy)
    assert aliases.get("delivery") == "submission"
    assert aliases.get("maintenance") == "inspection"

def test_vocab_preamble_injected_into_obligation_prompt(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", type("S", (), {"raw": {"domains": FINANCIAL_DOMAINS}})())
    fake_chunk = FakeChunk(text="The borrower shall repay the principal.", page_number=1)
    fake_doc = FakeDocument(doc_type=DocumentType.loan_agreement)
    prompt = extract_task._build_extraction_prompt("obligation_extraction", fake_chunk, fake_doc)
    assert "financial/insurance document" in prompt

def test_vocab_preamble_absent_for_unknown(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", type("S", (), {"raw": {"domains": FINANCIAL_DOMAINS}})())
    fake_chunk = FakeChunk(text="Some text.", page_number=1)
    fake_doc = FakeDocument(doc_type=DocumentType.unknown)
    prompt = extract_task._build_extraction_prompt("obligation_extraction", fake_chunk, fake_doc)
    assert "financial" not in prompt
```

Run → red.

### Implement

Remove module-level `_STAGE_KEYWORDS` and `_OBLIGATION_TYPE_ALIASES` dicts. Add:

```python
def _get_stage_keywords(stage_name: str, doc_type: DocumentType) -> tuple[str, ...]:
    domain_cfg = settings.raw.get("domains", {})
    for domain_data in domain_cfg.values():
        if doc_type.value in domain_data.get("doc_types", []):
            kw = domain_data.get("stage_keywords", {}).get(stage_name, [])
            return tuple(kw)
    general = domain_cfg.get("general", {})
    return tuple(general.get("stage_keywords", {}).get(stage_name, ()))

def _get_obligation_aliases(doc_type: DocumentType) -> dict[str, str]:
    domain_cfg = settings.raw.get("domains", {})
    for domain_data in domain_cfg.values():
        if doc_type.value in domain_data.get("doc_types", []):
            return domain_data.get("obligation_aliases", {})
    return domain_cfg.get("general", {}).get("obligation_aliases", {})

def _get_vocab_preamble(stage_name: str, doc_type: DocumentType) -> str:
    domain_cfg = settings.raw.get("domains", {})
    for domain_data in domain_cfg.values():
        if doc_type.value in domain_data.get("doc_types", []):
            return domain_data.get("vocab_preambles", {}).get(stage_name, "")
    return ""
```

Update `_relevance_score` to accept `doc_type: DocumentType` and use `_get_stage_keywords`. Thread `doc_type` through `_select_chunks_for_stage` and capture it in the closure inside each `_extract_*_impl`.

Update extraction prompt builder to inject preamble before schema:
```python
def _build_extraction_prompt(stage_name: str, chunk: Chunk, document: Document) -> str:
    schema = _STAGE_SCHEMAS[stage_name]
    vocab = _get_vocab_preamble(stage_name, document.doc_type)
    preamble = f"{vocab}\n\n" if vocab else ""
    return (
        f"Document type: {document.doc_type.value}\nPage: {chunk.page_number}\n\n"
        f"{preamble}{schema}\n\nChunk text:\n{chunk.text}"
    )
```

Update alias lookup in `_extract_obligations_impl`:
```python
aliases = _get_obligation_aliases(document.doc_type)
obligation_type = aliases.get(normalized, normalized)
```

Run → green. Run full suite → all passing.

---

## Phase 6 — Scoring (score.py)

### Tests first (red)

Add to `backend/tests/test_score_task.py`:

```python
SCORING_DOMAINS = {
    "financial": {
        "doc_types": ["insurance_policy", "loan_agreement", "deed_of_trust"],
        "doc_type_aligned": {
            "insurance_policy": ["payment", "compliance", "notification"],
            "loan_agreement": ["payment", "compliance", "submission"],
            "deed_of_trust": ["payment", "compliance"],
        },
    },
    "general": {
        "doc_types": ["unknown"],
        "doc_type_aligned": {},
    },
}

def test_doc_type_aligned_insurance_policy_payment_true(monkeypatch):
    monkeypatch.setattr(score_task, "settings", type("S", (), {"raw": {"domains": SCORING_DOMAINS}})())
    assert score_task._doc_type_aligned(DocumentType.insurance_policy, ObligationType.payment)

def test_doc_type_aligned_deed_of_trust_submission_false(monkeypatch):
    monkeypatch.setattr(score_task, "settings", type("S", (), {"raw": {"domains": SCORING_DOMAINS}})())
    assert not score_task._doc_type_aligned(DocumentType.deed_of_trust, ObligationType.submission)

def test_doc_type_aligned_unknown_always_true(monkeypatch):
    monkeypatch.setattr(score_task, "settings", type("S", (), {"raw": {"domains": SCORING_DOMAINS}})())
    assert score_task._doc_type_aligned(DocumentType.unknown, ObligationType.inspection)

def test_score_insurance_policy_payment_obligation_awards_alignment_bonus(monkeypatch):
    # Wire up FakeSession following the pattern in existing test_score_task.py:
    # FakeDocument(doc_type=DocumentType.insurance_policy),
    # FakeObligation(obligation_type=ObligationType.payment, evidence=[FakeEvidence(verified=True)])
    # monkeypatch settings.raw with SCORING_DOMAINS, monkeypatch SessionLocal
    # Call score_task.score_extractions({"document_id": ...})
    # Assert fake_obligation.system_confidence includes the 10-point doc_type_aligned bonus
    # (compare against a baseline run with ObligationType.submission which should NOT get the bonus)
    pass  # Codex: implement using existing FakeSession/FakeDocument patterns in test_score_task.py
```

Run → red.

### Implement

Replace `_doc_type_aligned` in `score.py`:
```python
def _doc_type_aligned(doc_type: DocumentType, obligation_type: ObligationType) -> bool:
    domain_cfg = settings.raw.get("domains", {})
    for domain_data in domain_cfg.values():
        aligned_map = domain_data.get("doc_type_aligned", {})
        if doc_type.value in aligned_map:
            expected = aligned_map[doc_type.value]
            if not expected:  # empty list = all types acceptable
                return True
            return obligation_type.value in expected
    return True  # no mapping found = any type acceptable (backward compat)
```

**Update existing score tests:** Tests that use `DocumentType.invoice` + `ObligationType.payment` now rely on the `construction` domain config in the real `config.yaml` (since `score_task` isn't monkeypatched for `settings`). Verify the `config.yaml` `domains.construction.doc_type_aligned.invoice` entry includes `payment`. If score tests monkeypatch `settings.raw`, add a minimal `domains` block.

Run → green. Run full suite → all passing.

---

## Phase 7 — Rescoring (rescore.py)

### Test first (red)

Read `rescore.py` first to find the existing prompt-building logic (it is likely inline in `rescore_with_llm` rather than a separate function). Extract prompt construction into a `_build_rescore_prompt(document, obligations, risks) -> str` helper, then test it:

```python
def test_rescore_prompt_uses_financial_persona_for_deed_of_trust():
    fake_doc = FakeDocument(doc_type=DocumentType.deed_of_trust, domain="financial")
    prompt = rescore_task._build_rescore_prompt(fake_doc, obligations=[], risks=[])
    assert "financial compliance analyst" in prompt

def test_rescore_prompt_uses_real_estate_persona():
    fake_doc = FakeDocument(doc_type=DocumentType.purchase_agreement, domain="real_estate")
    prompt = rescore_task._build_rescore_prompt(fake_doc, obligations=[], risks=[])
    assert "real estate attorney" in prompt

def test_rescore_prompt_defaults_to_document_analyst_when_domain_none():
    fake_doc = FakeDocument(doc_type=DocumentType.unknown, domain=None)
    prompt = rescore_task._build_rescore_prompt(fake_doc, obligations=[], risks=[])
    assert "document analyst" in prompt
```

Run → red (function doesn't exist yet / has wrong persona).

### Implement

In `rescore.py`, replace hardcoded persona:
```python
_DOMAIN_PERSONAS = {
    "construction": "construction contract analyst",
    "real_estate": "real estate attorney",
    "financial": "financial compliance analyst",
    "general": "document analyst",
}

def _build_rescore_prompt(document: Document, ...) -> str:
    persona = _DOMAIN_PERSONAS.get(document.domain or "general", "document analyst")
    # use persona in prompt template
```

Run → green. Run full suite → all passing.

---

## Phase 8 — API

### Tests first (red)
```python
def test_serialize_document_includes_domain():
    from backend.app.routers.assets import _serialize_document
    doc = FakeDocument(domain="financial", ...)
    result = _serialize_document(doc)
    assert result["domain"] == "financial"

def test_serialize_document_domain_null_for_old_docs():
    doc = FakeDocument(domain=None, ...)
    result = _serialize_document(doc)
    assert result["domain"] is None
```

Run → red.

### Implement

In `assets.py` `_serialize_document`, add:
```python
"domain": document.domain,
```

In `obligations.py` router, the list endpoint already loads `Obligation` rows. Add a join or subquery to fetch `Document.domain` for each obligation's `document_id`, then include it in the serialized row:
```python
"document_domain": document.domain,  # joined from Document table via obligation.document_id
```
Use SQLAlchemy's `.join(Document, Obligation.document_id == Document.id)` on the existing query, or load `Document` in a secondary query by `document_id`. Follow the pattern already used in other routers for cross-table lookups.

In `schemas/documents.py` `DocumentOut`:
```python
domain: Optional[str] = None
```

Run → green. Run full suite → all passing.

---

## Phase 9 — Frontend

No automated tests for frontend in this project. Manual verification steps:

**`frontend/src/lib/types.ts`:**
- Add `domain: string | null` to `DocumentSummary`, `DocumentDetail`, `Obligation`

**`frontend/src/app/assets/[id]/documents/page.tsx`:**
```typescript
const DOC_TYPES = [
  "all", "contract", "lease", "invoice", "inspection_report", "rfi", "change_order",
  "purchase_agreement", "title_commitment", "hoa_document", "disclosure_report",
  "insurance_policy", "loan_agreement", "deed_of_trust", "unknown"
] as const;
```
Add "Domain" `<th>` and `<td>` columns in the documents table. Display `doc.domain ?? "—"` with a small colored badge (use `construction` → gray, `real_estate` → green, `financial` → blue, `general`/null → muted).

**`frontend/src/app/documents/[id]/page.tsx`:**
Add to the metadata grid:
```tsx
<div>
  <p className="text-sm text-text-secondary">Domain</p>
  <p className="font-medium text-text-primary">{document.domain ?? "—"}</p>
</div>
```

**`frontend/src/app/obligations/ObligationsClientPage.tsx`:**
```typescript
const [domainFilter, setDomainFilter] = useState<string>("all");
const DOMAINS = ["all", "construction", "real_estate", "financial", "general"] as const;

// Filter sortedItems before render:
const visibleItems = domainFilter === "all"
  ? sortedItems
  : sortedItems.filter(o => o.document_domain === domainFilter);
```
Add a `<select>` control above the table with the `DOMAINS` options.

---

## End-to-End Verification

After all phases are green, verify manually:

1. **Upload a homeowner's insurance policy PDF**
   - Expect: `doc_type = insurance_policy`, `domain = financial`
   - Extraction prompt contains "financial/insurance document"
   - `payment` obligation gets `doc_type_aligned` bonus (+10)
   - UI shows "financial" domain badge

2. **Upload a purchase agreement PDF**
   - Expect: `doc_type = purchase_agreement`, `domain = real_estate`
   - Extraction prompt contains "real estate document"
   - UI shows "real_estate" domain badge

3. **Upload a deed of trust PDF**
   - Expect: `doc_type = deed_of_trust`, `domain = financial`
   - `submission` obligation does NOT get alignment bonus
   - `payment` obligation gets alignment bonus
   - Rescore prompt uses "financial compliance analyst"

4. **Re-process an existing `contract` document**
   - `domain` is set/updated to `"construction"`
   - All scores unchanged from before this feature

5. **Obligations page domain filter**
   - Selecting "financial" shows only obligations from insurance/loan/deed docs
   - Selecting "all" restores full list

6. **Run full pytest suite → 90+ tests, all green**
   ```bash
   python3 -m pytest -q backend/tests
   python3 -m compileall backend/app backend/alembic backend/tools -q
   ```

---

## Edge Cases

- **`domain = NULL` on existing docs:** API returns `null`; UI shows "—". Pipeline stages default to `general` at runtime via `_domain_for_doc_type` fallback.
- **Invoice currency regex:** Stays in code (cannot be a keyword list). Documented with a comment in `_heuristics_match`.
- **Postgres enum downgrade:** `ALTER TYPE ... ADD VALUE` values cannot be removed. Documented in migration `downgrade()` as a comment — column is droppable, enum values are not.
- **`_select_chunks_for_stage` signature change:** Thread `doc_type` as an explicit parameter, captured in the closure in `_extract_*_impl`. Do NOT use a combined `"stage:doc_type"` key string — it is confusing.
- **Missing `domains` key in config:** All lookups use `.get(key, {})` chains and fall back to `general`/empty. Add a startup `logging.warning` if `domains` is absent from `settings.raw`.
- **`rescore.py` null domain:** Use `document.domain or "general"` when looking up `_DOMAIN_PERSONAS`.
