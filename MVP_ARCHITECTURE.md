# MVP_ARCHITECTURE.md

**VeritasLayer — Authoritative Implementation Reference**
**Status:** Locked for MVP. This is the single source of truth for implementation.

**Implementation progress (as of 2026-03-12):**
- ✅ Stages 1–11: Ingest, Parse, OCR, Normalize, Chunk, Classify, Extract, Verify, Score, Persist, Notify — implemented and unit-tested (42 tests passing)
- ✅ Stage 11 behavior: `processing_complete` + conditional `risk_detected` event emission, recipient fanout, and channel fanout (`in_app`, optional `email`)
- ✅ DB schema: all 22 tables, single Alembic migration (head: `c03dec85f67a`)
- ✅ LLM wired: `services/llm.py` (LiteLLM) replaces stubs in stages 6–7; primary model `claude-sonnet-4-6`, fallbacks `gpt-4o`, `gemini-1.5-pro`
- 🔲 All routers except `/ingest`, `/documents`, `/health`
- 🔲 Auth, frontend, Celery Beat schedules, prompt registry

---

## 1. System Overview

VeritasLayer ingests operational documents (contracts, inspection reports, invoices, RFIs, change orders) and extracts structured, evidence-traceable obligations and risks. Every extracted item is anchored to a verbatim quote with page number, character offsets, and confidence score.

### Core Invariants

These are non-negotiable and must hold at every layer of the system:

1. **No evidence, no claim.** An obligation or risk cannot exist without at least one evidence record containing document_id, page_number, quote, char_start/char_end, and source.
2. **Immutable documents.** Uploaded files are never modified. Re-extraction creates new output records; originals are permanent.
3. **Human review required.** No item reaches `confirmed` status without explicit human approval. The system scores and suggests, but never auto-confirms. *(This overrides the additive scoring auto-confirm rule from the original spec — all items start as `needs_review` or `rejected`.)*
4. **System and human values are separate.** `system_confidence` and `reviewer_confidence` are distinct columns. Original extracted fields and reviewer edits are tracked independently via JSON diff in the reviews table. They are never merged.
5. **Extraction runs are versioned.** Every LLM call records prompt version, model, config snapshot, and timestamp. Deterministic stages (verification, scoring) must produce identical results given the same inputs.
6. **All intermediates are stored.** OCR text, chunks, classification results, raw LLM JSON output, and prompt versions are persisted. Nothing is silently discarded. Failed stages store their error.
7. **Quote-first extraction.** The LLM extracts verbatim quotes first, then interprets them into structured fields. Never the reverse.
8. **Precision over recall.** Minimize false positives. Accept missing real obligations over fabricated ones.

### Status Model (Resolved)

The system scores items and gates them into two tiers. Only human review can promote to `confirmed`:

| system_confidence | strong modality (must/shall/required) | Initial status |
|:-:|:-:|:-:|
| >= 80 | yes | `needs_review` (high priority — suggested approve) |
| >= 80 | no | `needs_review` |
| 50–79 | any | `needs_review` |
| < 50 | any | `rejected` (visible in rejected tier) |

Reviewer actions:
- **Approve** → status becomes `confirmed`
- **Approve with edits** → status becomes `confirmed`, edits recorded
- **Reject** → status becomes `rejected`

A reviewer can promote a `rejected` item to `confirmed` (override). A reviewer can demote a `needs_review` item to `rejected`. Human decisions are final and permanent.

---

## 2. Processing Pipeline

### 2.1 Stage Diagram

```
                          ┌──────────────────────────────────────────────┐
                          │              CELERY WORKER                   │
  ┌─────────┐             │                                              │
  │ FastAPI  │  dispatches │  ┌───────┐   ┌─────┐   ┌───────────┐       │
  │ POST     │────────────►│  │ PARSE │──►│ OCR │──►│ NORMALIZE │       │
  │ /ingest  │             │  └───────┘   └─────┘   └─────┬─────┘       │
  └─────────┘             │                                │             │
       │                  │  ┌───────┐   ┌──────────┐     │             │
       │ returns          │  │ CHUNK │◄──┘           │     │             │
       │ document_id      │  └───┬───┘               │     │             │
       │ immediately      │      │                   │     │             │
       ▼                  │  ┌───▼─────┐  ┌─────────┐│    │             │
                          │  │CLASSIFY │─►│ EXTRACT ││    │             │
                          │  └─────────┘  └────┬────┘│    │             │
                          │                    │     │     │             │
                          │  ┌────────┐  ┌─────▼───┐ │    │             │
                          │  │ SCORE  │◄─┤ VERIFY  │ │    │             │
                          │  └───┬────┘  └─────────┘ │    │             │
                          │      │                   │     │             │
                          │  ┌───▼────┐  ┌────────┐  │    │             │
                          │  │PERSIST │─►│ NOTIFY │  │    │             │
                          │  └────────┘  └────────┘  │    │             │
                          └──────────────────────────────────────────────┘
```

### 2.2 Stage Specifications

Each stage operates with **page x stage error isolation**: a failure at any page in any stage does not block other pages or stages. The document transitions to `partially_processed` if any page fails after exhausting retries.

#### Stage 1: INGEST (synchronous, in API process)

- Accept multipart PDF upload with required `asset_id`
- Validate: file is PDF or .txt, page count <= configurable max (default 500)
- Compute sha256; reject if duplicate exists
- Store original file to `/data/originals/{document_id}/{source_name}`
- Create `documents` record with `parse_status = uploaded`
- Return `document_id` immediately
- Dispatch Celery task `process_document(document_id)`

#### Stage 2: PARSE (Celery worker)

- Set `parse_status = parsing`
- PyMuPDF opens the original PDF
- For each page:
  - Extract text → `raw_text`
  - Extract layout spans with bbox → store in `text_spans`
  - Detect text vs scanned: if `len(raw_text.strip()) < 50` chars, flag as scanned
  - Detect tables: PyMuPDF layout analysis → set `has_tables = true`
  - Create `document_pages` record with `processing_status = pending`
- Set `documents.total_pages`, `documents.scanned_page_count`

#### Stage 3: OCR (Celery worker, conditional)

- Set `parse_status = ocr`
- Skip entirely if `scanned_page_count = 0`
- For each scanned page (batched in parallel, rate-limited via Celery):
  - Send to OLMOCR via DeepInfra API
  - On success: write `raw_text` from OCR output, set `text_source = ocr`
  - On failure: set `processing_status = failed`, store error, continue other pages
- Generate processed PDF with OCR text overlay → store at `/data/processed/{document_id}/`
- Update `documents.processed_file_path`
- If OLMOCR/DeepInfra is unavailable: all scanned pages fail, document becomes `partially_processed`. **No local OCR fallback in MVP.**

#### Stage 4: NORMALIZE (Celery worker)

- Set `parse_status = chunking` (normalize is a sub-step, not a separate status)
- For each page with `processing_status != failed`:
  - Collapse whitespace (runs of spaces/tabs/newlines → single space)
  - Unicode NFC normalization
  - Expand common ligatures (fi→fi, fl→fl, ff→ff, ffi→ffi, ffl→ffl)
  - Store result as `normalized_text`
  - Compute `text_sha256` from normalized_text
  - Set `processing_status = processed`

#### Stage 5: CHUNK (Celery worker)

- For each successfully processed page:
  - If `len(normalized_text)` <= token limit (configurable, default ~4000 chars): create one chunk with `split_reason = full_page`
  - If exceeds limit: split on section headers (lines matching `^\s*\d+[\.\)]\s+\w+` or all-caps lines) or clause boundaries. Set `split_reason = section_split` or `token_limit`
  - Each chunk stores `page_number`, `char_start`, `char_end` (relative to normalized_text), `chunk_sha256`

#### Stage 6: CLASSIFY (Celery worker, LLM call)

- Set `parse_status = classification`
- Create `extraction_runs` record: `stage = classification`, `status = running`
- Send first N pages (configurable, default 3) to LLM via LiteLLM
- LLM returns: `{doc_type, confidence, explanation}`
- Heuristic validation:
  - `invoice` → text must contain `$`, `USD`, `amount`, `total`, or currency patterns
  - `inspection_report` → must contain `inspect`, `examin`, `assess`, `finding`
  - `contract` → must contain `agree`, `party`, `parties`, `shall`, `obligation`
  - `rfi` → must contain `request for information`, `clarification`, `rfi`
  - `change_order` → must contain `change order`, `modification`, `amendment`
- If LLM and heuristics agree: set `doc_type` and `doc_type_confidence`
- If disagree: set `doc_type = unknown`, log disagreement
- Update extraction_run: `status = completed`, store `raw_llm_output`
- **LLM failure handling:** retry 3x with exponential backoff → fallback model (per-stage) → if all fail, set `doc_type = unknown` and continue

#### Stage 7: EXTRACT (Celery worker, LLM calls)

- Set `parse_status = extraction`
- Three sub-stages, each creating its own `extraction_runs` record:

**7a. Entity extraction:**
- LLM extracts party/entity names from chunks (quote-first)
- Each name fuzzy-matched against global `entities` registry (case-insensitive, Levenshtein ratio >= 0.85)
- High match → create `entity_mentions` with `suggested_entity_id`
- No match → create unresolved `entity_mentions`

**7b. Obligation extraction (per chunk):**
- LLM returns strict JSON array:
  ```json
  [{
    "quote": "verbatim text from document",
    "page_number": 5,
    "obligation_type": "payment",
    "modality": "shall",
    "due_date": "2025-06-15",
    "due_rule": null,
    "severity": "high",
    "responsible_party": "Contractor",
    "explanation": "Payment clause in section 4.2"
  }]
  ```
- Create `obligations` records with `status = needs_review`
- Link `extraction_run_id`

**7c. Risk extraction (per chunk):**
- Same pattern, LLM returns risks with `risk_type` from the 8-value enum
- Create `risks` records with `status = needs_review`
- Link `extraction_run_id`

**LLM failure handling applies per sub-stage:** retry 3x → fallback model → partial results (successful chunks saved, failed chunks logged).

#### Stage 8: VERIFY (Celery worker, deterministic — no LLM)

- Set `parse_status = verification`
- For each obligation and risk:

**8a. Normalized quote match:**
- Normalize the extracted quote with the same pipeline (whitespace, unicode, ligatures)
- Search for normalized quote as substring of `document_pages.normalized_text` for the given page
- If found: record `normalized_char_start`, `normalized_char_end`
- Map back to raw text: record `raw_char_start`, `raw_char_end` (via offset translation using the normalization mapping)
- If not found: set `status = rejected`, reason = `quote_not_found`
- Create evidence record (`obligation_evidence` or `risk_evidence`)

**8b. Modality check:**
- If `modality` is `should`, `may`, or `unknown`: apply -25 penalty (scored in stage 9)

**8c. Date parsing:**
- Absolute dates: parse to ISO format. If unparseable, set `due_kind = none`, apply -10 penalty
- Relative dates: store `due_rule` as text, set `due_kind = relative`
  - If trigger event detectable in same document (e.g., "date of this Agreement" and contract has a date): auto-compute `due_date`, set `due_kind = resolved_relative`
  - Otherwise: leave unresolved for reviewer

**8d. Doc-type constraints:**
- Invoices producing obligation_type other than `payment` → flag, do not reject, but note for reviewer

**8e. Duplicate suppression:**
- Unique key: `quote_sha256 + document_id + page_number + normalized_char_start + normalized_char_end`
- Duplicates within the same extraction run are dropped silently

**8f. Contradiction detection:**
- Within the same document, check for obligations with:
  - Same `obligation_type` but conflicting `due_date` values (both absolute, differ by > 0 days)
  - Same `obligation_type` + same `responsible_party` but conflicting `severity`
  - Payment obligations with different amounts in the quote text (regex: `\$[\d,]+\.?\d*`)
- If detected: set `contradiction_flag = true` on both obligations, create a `risk` of type `payment_term_conflict` (or relevant type) linking both via `risk_evidence`
- Create entry in `obligation_contradictions` junction table

**8g. External reference detection:**
- If quote contains phrases like "per Exhibit", "as defined in", "pursuant to", "referenced in", "attached hereto": set `has_external_reference = true`
- This does NOT change status but is surfaced in the review UI

#### Stage 9: SCORE (Celery worker, deterministic — no LLM)

- Set `parse_status = scoring`
- For each obligation and risk not already `rejected` by verification:
- Compute `system_confidence` using config-driven additive weights:

| Feature | Points | Condition |
|:--------|:------:|:----------|
| Quote verified | +40 | Required. If quote not found, item is already rejected |
| Strong modality | +15 | modality in (must, shall, required) |
| Due date resolved | +10 | due_kind in (absolute, resolved_relative) OR due_rule is present |
| Responsible party linked | +10 | responsible_entity_id is not null |
| Doc type aligned | +10 | obligation_type is expected for this doc_type |
| Verifier pass | +15 | No verification warnings |

| Penalty | Points | Condition |
|:--------|:------:|:----------|
| Weak modality | -25 | modality in (should, may) |
| OCR source | -15 | evidence.source = ocr |
| Contradiction | -30 | contradiction_flag = true |
| Missing deadline | -10 | obligation implies deadline but no due_date/due_rule |

- Apply status gating:
  - `system_confidence < 50` → `status = rejected`
  - `system_confidence >= 50` → `status = needs_review`
  - (No auto-confirm. `confirmed` requires human review.)

- Weights are loaded from YAML config, overridable via `config_overrides` DB table.

#### Stage 10: PERSIST (Celery worker)

- All records already written incrementally during stages 7-9
- Final step: update `documents.parse_status`:
  - All pages succeeded → `complete`
  - Some pages failed → `partially_processed`
  - Critical failure → `failed`
- Write `audit_log` entries for all created obligations, risks, evidence

#### Stage 11: NOTIFY (Celery worker)

- Emit `processing_complete` event → notify uploader + asset-assigned users
- If any risk has `severity` in (high, critical): emit `risk_detected` event
- Events written to `notification_events` table
- `user_notifications` created per recipient per channel (in_app + email if configured)
- Email sent via SMTP (configurable relay; production can use SES/SendGrid)

### 2.3 LLM Resilience

Applies to stages 6, 7 (classification, extraction):

```
Call LLM (primary model)
  ├── Success → continue
  └── Failure → retry (up to 3x, exponential backoff: 2s, 4s, 8s)
        ├── Retry succeeds → continue
        └── All retries exhausted → fallback to next model in chain
              ├── Fallback succeeds → continue (log model switch)
              └── All fallbacks exhausted → mark chunk/page as failed
                    └── Continue with other chunks/pages (partial results)
```

**Constraint:** fallback happens per-stage only. If classification uses model A, and extraction starts with model A but fails mid-way, extraction falls back to model B for ALL remaining chunks in that stage. Never mix models within a single stage for one document.

### 2.4 Document Lifecycle State Machine

```
uploaded ──► parsing ──► ocr ──► chunking ──► classification ──► extraction ──► verification ──► scoring ──► complete
   │            │         │         │              │                 │               │              │
   └────────────┴─────────┴─────────┴──────────────┴─────────────────┴───────────────┴──────────────┘
                                            │
                                   failed / partially_processed
```

`parse_status` tracks the current stage. The API exposes `GET /documents/{id}/status` for frontend polling, returning `{stage, pages_processed, pages_total, pages_failed}`.

---

## 3. Database Schema

### 3.1 Entity-Relationship Overview

```
assets ─────────────────┬──────────────── user_asset_assignments ── users
  │                     │                                            │
  │ 1:N                 │ N:M                                        │
  ▼                     │                                            │
documents ──────────────┘                                            │
  │                                                                  │
  ├── 1:N → document_pages                                           │
  ├── 1:N → text_spans                                               │
  ├── 1:N → chunks                                                   │
  ├── 1:N → extraction_runs → prompt_versions                        │
  ├── 1:N → entity_mentions ──→ entities (global)                    │
  │                                                                  │
  ├── 1:N → obligations ──→ 1:N → obligation_evidence                │
  │              │                                                   │
  │              ├── FK → entities (responsible_entity_id)            │
  │              ├── FK → extraction_runs                             │
  │              └── N:M → obligation_contradictions (self-join)      │
  │                                                                  │
  ├── 1:N → risks ──→ 1:N → risk_evidence                           │
  │              └── FK → extraction_runs                             │
  │                                                                  │
  ├── 1:N → obligation_reviews ──→ FK users (reviewer_id) ◄──────────┘
  └── 1:N → risk_reviews ──→ FK users (reviewer_id) ◄────────────────┘

Standalone:
  config_overrides    (key-value, admin-editable)
  audit_log           (append-only change log)
  notification_events → user_notifications → users
```

### 3.2 Table Definitions

All primary keys are `uuid` (generated as UUIDv4). All timestamps are `timestamptz`.

---

#### `users`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| email | text | UNIQUE, NOT NULL |
| name | text | NOT NULL |
| oidc_provider | text | NOT NULL — "google" or "microsoft" |
| oidc_subject | text | NOT NULL — provider's unique subject ID |
| role | enum | NOT NULL — admin, reviewer, viewer |
| is_active | bool | NOT NULL, DEFAULT true |
| created_at | timestamptz | NOT NULL, DEFAULT now() |
| last_login_at | timestamptz | |

UNIQUE constraint on `(oidc_provider, oidc_subject)`.
First user to log in auto-becomes admin.

---

#### `assets`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| name | text | NOT NULL |
| description | text | |
| created_by | uuid | FK users.id, NOT NULL |
| created_at | timestamptz | NOT NULL, DEFAULT now() |
| updated_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `user_asset_assignments`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| user_id | uuid | FK users.id, NOT NULL |
| asset_id | uuid | FK assets.id, NOT NULL |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

UNIQUE constraint on `(user_id, asset_id)`.

---

#### `documents`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| asset_id | uuid | FK assets.id, NOT NULL |
| source_name | text | NOT NULL — original filename |
| file_path | text | NOT NULL — path to original |
| processed_file_path | text | — OCR overlay PDF |
| sha256 | text | UNIQUE, NOT NULL |
| mime_type | text | NOT NULL |
| uploaded_by | uuid | FK users.id, NOT NULL |
| uploaded_at | timestamptz | NOT NULL, DEFAULT now() |
| doc_type | enum | NOT NULL, DEFAULT 'unknown' — contract, inspection_report, rfi, change_order, invoice, unknown |
| doc_type_confidence | float | |
| doc_date | date | |
| parse_status | enum | NOT NULL, DEFAULT 'uploaded' — uploaded, parsing, ocr, chunking, classification, extraction, verification, scoring, complete, partially_processed, failed |
| total_pages | int | |
| scanned_page_count | int | NOT NULL, DEFAULT 0 |
| notes | text | |

---

#### `document_pages`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| document_id | uuid | FK documents.id, NOT NULL, ON DELETE CASCADE |
| page_number | int | NOT NULL |
| raw_text | text | NOT NULL, DEFAULT '' |
| normalized_text | text | NOT NULL, DEFAULT '' |
| text_source | enum | NOT NULL — pdf_text, ocr |
| text_sha256 | text | NOT NULL |
| width | float | |
| height | float | |
| has_tables | bool | NOT NULL, DEFAULT false |
| processing_status | enum | NOT NULL, DEFAULT 'pending' — pending, processed, failed |
| processing_error | text | |

UNIQUE constraint on `(document_id, page_number)`.

---

#### `text_spans`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| document_id | uuid | FK documents.id, NOT NULL, ON DELETE CASCADE |
| page_number | int | NOT NULL |
| char_start | int | NOT NULL |
| char_end | int | NOT NULL |
| bbox_x1 | float | NOT NULL |
| bbox_y1 | float | NOT NULL |
| bbox_x2 | float | NOT NULL |
| bbox_y2 | float | NOT NULL |
| span_text | text | NOT NULL |
| span_sha256 | text | NOT NULL |

Index on `(document_id, page_number)`.

---

#### `chunks`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| document_id | uuid | FK documents.id, NOT NULL, ON DELETE CASCADE |
| page_number | int | NOT NULL |
| char_start | int | NOT NULL |
| char_end | int | NOT NULL |
| text | text | NOT NULL |
| embedding | jsonb | — nullable, reserved for future pgvector use |
| chunk_sha256 | text | NOT NULL |
| split_reason | enum | NOT NULL — full_page, section_split, token_limit |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

Index on `(document_id, page_number)`.

---

#### `entities`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| canonical_name | text | UNIQUE, NOT NULL |
| entity_type | enum | NOT NULL — party, person, org, location, system, other |
| aliases | jsonb | NOT NULL, DEFAULT '[]' |
| created_at | timestamptz | NOT NULL, DEFAULT now() |
| updated_at | timestamptz | NOT NULL, DEFAULT now() |

Global registry. Not scoped per-document.

---

#### `entity_mentions`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| entity_id | uuid | FK entities.id — null until resolved |
| document_id | uuid | FK documents.id, NOT NULL |
| mentioned_name | text | NOT NULL — as extracted from document |
| page_number | int | NOT NULL |
| suggested_entity_id | uuid | FK entities.id — system's fuzzy match suggestion |
| resolved | bool | NOT NULL, DEFAULT false |
| resolved_by | uuid | FK users.id |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `prompt_versions`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| prompt_name | text | NOT NULL — e.g., "extract_obligations_contract" |
| version | int | NOT NULL |
| template | text | NOT NULL |
| doc_type | enum | — null for universal prompts |
| description | text | |
| is_active | bool | NOT NULL, DEFAULT false |
| created_by | uuid | FK users.id, NOT NULL |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

UNIQUE constraint on `(prompt_name, version)`.

---

#### `extraction_runs`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| document_id | uuid | FK documents.id, NOT NULL |
| prompt_version_id | uuid | FK prompt_versions.id, NOT NULL |
| model_used | text | NOT NULL — e.g., "gpt-4o" |
| config_snapshot | jsonb | NOT NULL — scoring weights/thresholds at time of extraction |
| stage | enum | NOT NULL — classification, entity_extraction, obligation_extraction, risk_extraction |
| status | enum | NOT NULL, DEFAULT 'running' — running, completed, failed, superseded |
| started_at | timestamptz | NOT NULL, DEFAULT now() |
| completed_at | timestamptz | |
| error | text | |
| raw_llm_output | jsonb | — stored for debugging |

Index on `(document_id, stage)`.

---

#### `obligations`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| document_id | uuid | FK documents.id, NOT NULL |
| obligation_type | enum | NOT NULL — compliance, submission, payment, inspection, notification, other |
| obligation_text | text | NOT NULL |
| modality | enum | NOT NULL — must, shall, required, should, may, unknown |
| responsible_entity_id | uuid | FK entities.id |
| due_kind | enum | NOT NULL — absolute, relative, resolved_relative, none |
| due_date | date | |
| due_rule | text | — e.g., "within 10 days of notice" |
| trigger_date | date | — manually entered for resolved relative deadlines |
| severity | enum | NOT NULL — low, medium, high, critical |
| status | enum | NOT NULL, DEFAULT 'needs_review' — needs_review, confirmed, rejected |
| system_confidence | int | NOT NULL, CHECK (0 <= system_confidence <= 100) |
| reviewer_confidence | int | CHECK (0 <= reviewer_confidence <= 100) |
| has_external_reference | bool | NOT NULL, DEFAULT false |
| contradiction_flag | bool | NOT NULL, DEFAULT false |
| extraction_run_id | uuid | FK extraction_runs.id |
| created_at | timestamptz | NOT NULL, DEFAULT now() |
| updated_at | timestamptz | NOT NULL, DEFAULT now() |

Index on `(document_id, status)`.
Index on `(status, severity)` for review queue queries.
Index on `(due_date)` WHERE `due_date IS NOT NULL` for deadline notifications.

---

#### `risks`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| document_id | uuid | FK documents.id, NOT NULL |
| risk_type | enum | NOT NULL — missing_required_document, expired_certificate_or_insurance, inspection_failed_reinspection_required, approval_overdue, payment_term_conflict, scope_change_indicator, schedule_dependency_blocker, unknown_risk |
| risk_text | text | NOT NULL |
| severity | enum | NOT NULL — low, medium, high, critical |
| status | enum | NOT NULL, DEFAULT 'needs_review' — needs_review, confirmed, rejected |
| system_confidence | int | NOT NULL, CHECK (0 <= system_confidence <= 100) |
| reviewer_confidence | int | CHECK (0 <= reviewer_confidence <= 100) |
| has_external_reference | bool | NOT NULL, DEFAULT false |
| contradiction_flag | bool | NOT NULL, DEFAULT false |
| extraction_run_id | uuid | FK extraction_runs.id |
| created_at | timestamptz | NOT NULL, DEFAULT now() |
| updated_at | timestamptz | NOT NULL, DEFAULT now() |

Index on `(document_id, status)`.

---

#### `obligation_contradictions`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| obligation_a_id | uuid | FK obligations.id, NOT NULL |
| obligation_b_id | uuid | FK obligations.id, NOT NULL |
| risk_id | uuid | FK risks.id, NOT NULL — the auto-generated contradiction risk |
| detected_at | timestamptz | NOT NULL, DEFAULT now() |

CHECK constraint: `obligation_a_id < obligation_b_id` (canonical ordering, prevents duplicate pairs).
UNIQUE constraint on `(obligation_a_id, obligation_b_id)`.

---

#### `obligation_evidence`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| obligation_id | uuid | FK obligations.id, NOT NULL, ON DELETE CASCADE |
| document_id | uuid | FK documents.id, NOT NULL |
| page_number | int | NOT NULL |
| quote | text | NOT NULL |
| quote_sha256 | text | NOT NULL |
| raw_char_start | int | NOT NULL |
| raw_char_end | int | NOT NULL |
| normalized_char_start | int | NOT NULL |
| normalized_char_end | int | NOT NULL |
| bbox_x1 | float | |
| bbox_y1 | float | |
| bbox_x2 | float | |
| bbox_y2 | float | |
| source | enum | NOT NULL — pdf_text, ocr |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

UNIQUE constraint on `(quote_sha256, document_id, page_number, normalized_char_start, normalized_char_end)` for dedup.

---

#### `risk_evidence`

Same schema as `obligation_evidence` but with `risk_id` FK instead of `obligation_id`.

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| risk_id | uuid | FK risks.id, NOT NULL, ON DELETE CASCADE |
| document_id | uuid | FK documents.id, NOT NULL |
| page_number | int | NOT NULL |
| quote | text | NOT NULL |
| quote_sha256 | text | NOT NULL |
| raw_char_start | int | NOT NULL |
| raw_char_end | int | NOT NULL |
| normalized_char_start | int | NOT NULL |
| normalized_char_end | int | NOT NULL |
| bbox_x1 | float | |
| bbox_y1 | float | |
| bbox_x2 | float | |
| bbox_y2 | float | |
| source | enum | NOT NULL — pdf_text, ocr |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `obligation_reviews`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| obligation_id | uuid | FK obligations.id, NOT NULL |
| decision | enum | NOT NULL — approve, reject, edit_approve |
| reviewer_id | uuid | FK users.id, NOT NULL |
| field_edits | jsonb | — e.g., {"due_date": {"old": "2025-03-15", "new": "2025-03-30"}} |
| reviewer_confidence | int | CHECK (0 <= reviewer_confidence <= 100) |
| reason | text | |
| decided_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `risk_reviews`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| risk_id | uuid | FK risks.id, NOT NULL |
| decision | enum | NOT NULL — approve, reject, edit_approve |
| reviewer_id | uuid | FK users.id, NOT NULL |
| field_edits | jsonb | |
| reviewer_confidence | int | CHECK (0 <= reviewer_confidence <= 100) |
| reason | text | |
| decided_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `config_overrides`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| key | text | UNIQUE, NOT NULL — dotted path, e.g., "scoring.quote_match_weight" |
| value | jsonb | NOT NULL |
| updated_by | uuid | FK users.id, NOT NULL |
| updated_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `audit_log`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| table_name | text | NOT NULL |
| record_id | uuid | NOT NULL |
| action | enum | NOT NULL — create, update, delete |
| old_values | jsonb | |
| new_values | jsonb | |
| performed_by | uuid | FK users.id |
| performed_at | timestamptz | NOT NULL, DEFAULT now() |

Append-only. No updates or deletes on this table.
Index on `(table_name, record_id)`.
Index on `(performed_at)`.

Tables that must log to audit_log: `obligations`, `risks`, `obligation_reviews`, `risk_reviews`, `entities`, `entity_mentions` (on resolve), `config_overrides`.

---

#### `notification_events`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| event_type | enum | NOT NULL — processing_complete, deadline_approaching, items_awaiting_review, risk_detected, weekly_summary_ready |
| payload | jsonb | NOT NULL |
| created_at | timestamptz | NOT NULL, DEFAULT now() |

---

#### `user_notifications`

| Column | Type | Constraints |
|--------|------|------------|
| id | uuid | PK |
| user_id | uuid | FK users.id, NOT NULL |
| event_id | uuid | FK notification_events.id, NOT NULL |
| channel | enum | NOT NULL — in_app, email |
| status | enum | NOT NULL, DEFAULT 'pending' — pending, sent, read |
| sent_at | timestamptz | |
| read_at | timestamptz | |

Index on `(user_id, status)` for notification bell queries.

---

### 3.3 Table Count

**22 tables total:** users, assets, user_asset_assignments, documents, document_pages, text_spans, chunks, entities, entity_mentions, prompt_versions, extraction_runs, obligations, risks, obligation_contradictions, obligation_evidence, risk_evidence, obligation_reviews, risk_reviews, config_overrides, audit_log, notification_events, user_notifications.

### 3.4 Database Invariants (Enforced at Application Layer)

1. `obligation_evidence.quote` must equal `document_pages.normalized_text[normalized_char_start:normalized_char_end]` for the matching page
2. Every obligation must have `>= 1` obligation_evidence record
3. Every risk must have `>= 1` risk_evidence record
4. `system_confidence` is never null on obligations or risks
5. `status = confirmed` requires at least one `obligation_reviews` / `risk_reviews` record with `decision in (approve, edit_approve)`
6. `audit_log` written for every create/update/delete on tracked tables

---

## 4. Services and Components

### 4.1 Service Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Docker Compose                              │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐  │
│  │ FastAPI   │    │ Celery   │    │  Redis   │    │  Postgres    │  │
│  │ (uvicorn) │◄──►│ Worker   │◄──►│ (broker) │    │  (database)  │  │
│  │ port 8000 │    │          │    │ port 6379│    │  port 5432   │  │
│  └─────┬────┘    └──────────┘    └──────────┘    └──────────────┘  │
│        │                                                            │
│  ┌─────▼────────┐                                                   │
│  │  SvelteKit   │                                                   │
│  │  (frontend)  │                                                   │
│  │  port 5173   │                                                   │
│  └──────────────┘                                                   │
│                                                                     │
│  ┌──────────────────────┐                                           │
│  │  /data               │                                           │
│  │  ├── originals/      │  (mounted volume)                         │
│  │  └── processed/      │                                           │
│  └──────────────────────┘                                           │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 FastAPI Application (backend)

```
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, middleware
│   ├── config.py            # Layered config loader (YAML + DB + env)
│   ├── database.py          # SQLAlchemy engine, session factory
│   ├── models/              # SQLAlchemy ORM models (all enums in enums.py)
│   │   ├── base.py          # Base, UUIDPrimaryKeyMixin
│   │   ├── enums.py         # All enums
│   │   ├── document.py      # Document, DocumentPage, TextSpan, Chunk
│   │   ├── obligation.py    # Obligation, ObligationEvidence, ObligationContradictions
│   │   ├── risk.py          # Risk, RiskEvidence
│   │   ├── entity.py        # Entity, EntityMention
│   │   ├── user.py          # User, Asset, UserAssetAssignment
│   │   ├── extraction.py    # ExtractionRun, PromptVersion
│   │   ├── notification.py  # NotificationEvent, UserNotification
│   │   ├── audit.py         # AuditLog
│   │   └── config.py        # ConfigOverride
│   ├── schemas/             # Pydantic request/response models
│   ├── routers/             # FastAPI route handlers
│   │   ├── ingest.py        # ✅ Implemented
│   │   ├── documents.py     # ✅ Implemented
│   │   ├── obligations.py   # Not yet implemented
│   │   ├── risks.py         # Not yet implemented
│   │   ├── reviews.py       # Not yet implemented
│   │   ├── entities.py      # Not yet implemented
│   │   ├── summaries.py     # Not yet implemented
│   │   ├── auth.py          # Not yet implemented
│   │   ├── users.py         # Not yet implemented
│   │   ├── notifications.py # Not yet implemented
│   │   └── config.py        # Not yet implemented
│   ├── auth/                # OIDC integration (Google, Microsoft) — not yet implemented
│   ├── services/            # Business logic layer
│   │   ├── normalization.py # ✅ Implemented
│   │   ├── chunking.py      # ✅ Implemented
│   │   ├── ocr.py           # ✅ Implemented (DeepInfra OLMOCR)
│   │   ├── storage.py       # ✅ Implemented (LocalStorage)
│   │   ├── scoring.py       # Not yet implemented
│   │   └── entity_matching.py # Not yet implemented
│   └── worker/              # Celery app + task definitions
│       ├── celery_app.py
│       ├── tasks/
│       │   ├── _helpers.py  # update_parse_status (failed-state guard)
│       │   ├── parse.py     # ✅ Stage 2 implemented
│       │   ├── ocr.py       # ✅ Stage 3 implemented
│       │   ├── chunk.py     # ✅ Stages 4+5 implemented (normalize_pages, chunk_pages)
│       │   ├── classify.py  # ✅ Stage 6 implemented
│       │   ├── extract.py   # ✅ Stage 7 implemented
│       │   ├── verify.py    # ✅ Stage 8 implemented
│       │   ├── score.py     # ✅ Stage 9 implemented
│       │   └── notify.py    # ✅ Stages 10+11 implemented
│       └── pipeline.py      # Orchestrator: synchronous chain
├── alembic/                 # Database migrations (head: c03dec85f67a)
├── config.yaml              # Default configuration
├── requirements.txt
└── tests/
    ├── test_normalization.py
    ├── test_chunking.py
    ├── test_ocr_service.py
    ├── test_pipeline_tasks.py
    ├── test_classification_task.py
    ├── test_extraction_tasks.py
    ├── test_verify_task.py
    ├── test_score_task.py
    ├── test_notify_task.py
    └── test_llm_service.py      # LiteLLM wrapper + JSON parsing tests
```

### 4.3 Celery Worker

**Broker:** Redis
**Result backend:** Redis
**Queues:**
- `default` — pipeline orchestration, scoring, verification, notifications
- `llm` — all LLM calls (classification, extraction). Separate queue for concurrency control
- `ocr` — OLMOCR calls. Separate queue for rate limiting

**Celery Beat schedule (MVP):**
- `check_deadlines` — daily at 08:00 UTC. Finds obligations with due_date within 7 or 1 day(s), emits `deadline_approaching` events
- `review_digest` — daily at 09:00 UTC. Counts `needs_review` items per asset, emits `items_awaiting_review` events to assigned reviewers
- `generate_weekly_summary` — weekly Monday at 07:00 UTC. Generates structured JSON + LLM narrative per asset

### 4.4 Configuration Layering

```
  Priority (highest wins)
  ┌────────────────────────┐
  │  Environment variables │ ← secrets only (DATABASE_URL, API keys, OIDC secrets)
  ├────────────────────────┤
  │  config_overrides (DB) │ ← admin-editable at runtime
  ├────────────────────────┤
  │  config.yaml (file)    │ ← version-controlled defaults
  └────────────────────────┘

  Config loader reads all three layers and merges.
  Env vars override DB overrides override YAML defaults.
```

### 4.5 LLM Integration

All LLM calls go through LiteLLM. Configuration:

```yaml
llm:
  primary_model: "gpt-4o"
  fallback_models:
    - "claude-sonnet-4-20250514"
    - "gemini-1.5-pro"
  max_retries: 3
  retry_backoff_base: 2  # seconds
```

Configurable per deployment: cloud APIs, enterprise zero-retention tiers, or self-hosted (vLLM/Ollama). LiteLLM abstracts the provider.

### 4.6 Prompt Registry

```
/prompts
  /classification
    v1.yaml
    v2.yaml
  /extraction
    /contract
      obligations_v1.yaml
      risks_v1.yaml
    /inspection_report
      obligations_v1.yaml
      risks_v1.yaml
    /invoice
      obligations_v1.yaml
    ...
  /entity_extraction
    v1.yaml
```

Each YAML file contains: `template`, `model_compatibility`, `description`, `doc_types`.
Active versions tracked in `prompt_versions` table. Each extraction_run references the prompt version used.

**Selective re-extraction:** admin triggers `POST /documents/{id}/reextract` with a prompt version. Old results archived via audit_log, new extraction_runs created, old runs marked `superseded`.

---

## 5. Async Processing Model

### 5.1 Task Flow

```python
# Simplified Celery task chain
@celery_app.task
def process_document(document_id: str):
    """Orchestrator — runs the full pipeline."""
    parse_document(document_id)       # Stage 2
    ocr_scanned_pages(document_id)    # Stage 3 (conditional)
    normalize_pages(document_id)      # Stage 4
    chunk_pages(document_id)          # Stage 5
    classify_document(document_id)    # Stage 6 (LLM)
    extract_entities(document_id)     # Stage 7a (LLM)
    extract_obligations(document_id)  # Stage 7b (LLM)
    extract_risks(document_id)        # Stage 7c (LLM)
    verify_extractions(document_id)   # Stage 8
    score_extractions(document_id)    # Stage 9
    persist_final_status(document_id) # Stage 10
    emit_notifications(document_id)   # Stage 11
```

In practice, each stage is a separate Celery task chained together. If a stage fails after all retries, the orchestrator catches the error, marks the document appropriately, and still runs notify.

### 5.2 Concurrency Controls

| Queue | Default concurrency | Rate limit | Purpose |
|-------|:---:|:---:|---------|
| `default` | 4 workers | none | Parse, normalize, chunk, verify, score, persist, notify |
| `llm` | 2 workers | 10/minute | Classification, extraction (all LLM calls) |
| `ocr` | 2 workers | 20/minute | OLMOCR API calls |

All values configurable via `config.yaml`.

### 5.3 Progress Tracking

Frontend polls `GET /documents/{id}/status` which returns:

```json
{
  "document_id": "...",
  "parse_status": "extraction",
  "total_pages": 47,
  "pages_processed": 32,
  "pages_failed": 1,
  "current_stage": "Extracting obligations",
  "started_at": "2025-03-15T10:00:00Z",
  "estimated_completion": null
}
```

Updated by each stage as it processes pages.

---

## 6. Frontend MVP Screens

### 6.1 Screen Inventory

| # | Screen | Route | Access | Purpose |
|:-:|--------|-------|--------|---------|
| 1 | Login | `/login` | Public | OIDC sign-in (Google / Microsoft buttons) |
| 2 | Asset List | `/` | All authenticated | Top-level navigation. Asset cards: name, doc count, pending review count |
| 3 | Document List | `/assets/{id}/documents` | Asset-assigned users | Document table: name, type, status, upload date, page count. Upload dropzone. Filter by doc_type, parse_status |
| 4 | Document Detail | `/documents/{id}` | Asset-assigned users | Processing status banner (polling). Tabs: Obligations, Risks, Pages. Counts per status tier |
| 5 | Obligations Table | `/obligations` | Asset-assigned users | Cross-asset table with filters: status, severity, asset, document, due date range. Sortable columns. Inline review action buttons |
| 6 | Risks Table | `/risks` | Asset-assigned users | Same pattern as obligations table. Filter by risk_type, severity, status |
| 7 | Evidence Viewer | `/obligations/{id}/evidence` or `/risks/{id}/evidence` | Asset-assigned users | Split panel: left = PDF page via pdf.js with bbox highlight rectangles (falls back to text-with-highlight when no bbox). Right = item detail + quote in context |
| 8 | Review Modal | (overlay on tables) | Reviewer, Admin | Decision: approve / edit+approve / reject. Editable fields: due_date, severity, responsible_party, obligation_text. Confidence slider. Reason text area |
| 9 | Entity Management | `/entities` | Reviewer, Admin | Global entity list. Pending suggestions queue (accept/reject/reassign). Merge dialog (select two entities, confirm). Alias editor |
| 10 | Deadline View | `/deadlines` | Asset-assigned users | Obligations sorted by due_date. Grouped: overdue, next 7 days, next 14 days, next 30 days. Link to evidence viewer |
| 11 | Notifications | (bell dropdown) | All authenticated | Notification list: event type icon, message, timestamp, read/unread. Mark as read. Click navigates to relevant item |
| 12 | Admin: Users | `/admin/users` | Admin only | User table: email, name, role, last login. Role dropdown. Asset assignment checkboxes |
| 13 | Admin: Config | `/admin/config` | Admin only | Key-value editor for config_overrides. Shows current effective value (merged from YAML + DB). Edit/reset to default |

### 6.2 Not in MVP Frontend

- Calendar/Gantt view for deadlines
- Advanced analytics or charts
- Webhook management UI (admin via API/CLI only)
- Prompt editor UI (managed via files + DB, not frontend)
- Batch operations (bulk approve/reject)
- Document comparison/diff view
- Export to PDF/Excel

### 6.3 Frontend-Backend Communication

- **Protocol:** REST API, JSON payloads
- **Auth:** OIDC tokens passed as `Authorization: Bearer {token}` headers
- **Processing progress:** polling `GET /documents/{id}/status` every 3 seconds while `parse_status` is not terminal
- **Pagination:** cursor-based for all list endpoints
- **PDF rendering:** pdf.js for the evidence viewer. PDF served from `/data/originals/` (or `/data/processed/` for OCR'd docs) via a backend endpoint that validates access

---

## 7. MVP Boundary

### IN Scope — Must ship

| Category | Feature |
|----------|---------|
| **Ingestion** | PDF upload (text + scanned), sha256 dedup, asset assignment, page limit validation |
| **OCR** | OLMOCR via DeepInfra, auto-detect scanned pages, batch processing, confidence penalty |
| **Parsing** | PyMuPDF text + bbox extraction, text normalization (whitespace, unicode, ligatures), table detection/flagging |
| **Chunking** | Page-based with section splitting, token limit enforcement |
| **Classification** | LLM + heuristic validation, 6 doc types |
| **Extraction** | Quote-first obligations + risks + entities, strict JSON, per-chunk processing |
| **Verification** | Normalized quote match, modality check, date parsing, dedup, contradiction detection, external reference flagging |
| **Scoring** | Config-driven additive weights, dual scoring (system + reviewer), two-tier gating (needs_review / rejected) |
| **Review** | Approve / approve-with-edits / reject, JSON diff tracking, reviewer confidence |
| **Entities** | Global registry, fuzzy matching with suggestions, manual merge, alias management |
| **Relative dates** | Auto-resolve from same-doc context, manual trigger date input |
| **Notifications** | 5 event types, in-app + email, asset-scoped targeting |
| **Weekly summary** | Priority-based structured JSON + LLM narrative, Celery Beat scheduled |
| **Auth** | OAuth 2.0 / OIDC (Google + Microsoft), 3 roles, asset-scoped access |
| **Audit** | Change log for all mutations on tracked tables |
| **Config** | YAML defaults + DB overrides + env vars for secrets |
| **Prompt mgmt** | Versioned files, DB tracking, selective re-extraction |
| **LLM resilience** | Retry + per-stage fallback + partial results |
| **Frontend** | 13 screens (see §6.1) |
| **Testing** | 3-tier: unit (mocked LLM), integration (property-based), golden fixtures |
| **Deployment** | Docker Compose for dev, Dockerfiles for prod |

### OUT of Scope — Explicitly deferred

| Category | What | Why deferred |
|----------|------|-------------|
| **Chat** | Conversational interface over the truth layer | Build after truth layer is trustworthy |
| **pgvector** | Embedding search, RAG | No concrete use case yet. Column reserved |
| **Document lineage** | Auto-detect that v2 supersedes v1 | Requires similarity matching infra |
| **Cross-doc resolution** | Resolve "per Exhibit B" by linking to Exhibit B | Requires document relationship graph |
| **Auto-confirm** | System automatically setting status to confirmed | Contradicts human review authority principle |
| **Entity auto-merge** | System merging entities without reviewer | Too risky for false merges |
| **Webhooks** | External event integrations | Architecture is webhook-ready; expose when needed |
| **Local OCR** | Tesseract/local fallback when DeepInfra is down | Adds complexity; fail + retry is acceptable |
| **Learned scoring** | ML model for confidence weights | Start with config-driven, collect review data first |
| **Multi-tenancy** | Tenant isolation across orgs | Single-tenant with auth is sufficient for MVP |
| **Calendar UI** | Gantt/calendar view for deadlines | List view sufficient for MVP |
| **Batch operations** | Bulk approve/reject/export | Single-item review sufficient for MVP |
| **Legal compliance** | Legal advice, automated negotiation | Explicit non-goal |
| **Enterprise analytics** | Dashboards, trend analysis, reporting | Explicit non-goal |
| **Non-PDF inputs** | Word, Excel, email | PDF + .txt only for MVP |

---

## 8. API Endpoint Reference

### Documents

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/ingest` | Reviewer+ | Multipart upload. Required: `asset_id`. Returns `document_id` |
| GET | `/documents/{id}` | Asset user | Metadata + processing status |
| GET | `/documents/{id}/status` | Asset user | Polling: stage, pages processed/failed/total |
| GET | `/documents/{id}/pages/{page}` | Asset user | Page text + text_spans |
| GET | `/documents/{id}/pdf` | Asset user | Serve original or processed PDF for viewer |
| POST | `/documents/{id}/reextract` | Admin | Trigger re-extraction with prompt version |

### Obligations & Risks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/obligations` | Asset user | List with filters: status, severity, document_id, asset_id, due_date range. Cursor pagination |
| GET | `/obligations/{id}` | Asset user | Full obligation with evidence inline |
| GET | `/risks` | Asset user | List with filters: status, severity, risk_type, document_id, asset_id |
| GET | `/risks/{id}` | Asset user | Full risk with evidence inline |

### Reviews

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/obligations/{id}/review` | Reviewer+ | Approve / edit_approve / reject |
| POST | `/risks/{id}/review` | Reviewer+ | Approve / edit_approve / reject |

### Entities

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/entities` | Asset user | Global entity registry |
| GET | `/entities/suggestions` | Reviewer+ | Pending merge suggestions |
| POST | `/entities/{id}/merge` | Reviewer+ | Merge entity B into entity A |
| POST | `/entity-mentions/{id}/resolve` | Reviewer+ | Assign mention to entity |

### Summaries

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/summary/weekly` | Asset user | Structured JSON digest. Filter by asset_id |
| GET | `/summary/weekly/narrative` | Asset user | LLM-generated narrative. Filter by asset_id |

### Assets

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/assets` | Authenticated | List user's assigned assets (admins see all) |
| POST | `/assets` | Admin | Create asset |
| GET | `/assets/{id}` | Asset user | Asset detail with doc count, obligation/risk counts |

### Auth & Users

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/auth/login/{provider}` | Public | Initiate OIDC (google or microsoft) |
| GET | `/auth/callback` | Public | OIDC callback, creates/updates user, returns token |
| GET | `/users/me` | Authenticated | Current user profile |
| GET | `/users` | Admin | List all users |
| PUT | `/users/{id}/role` | Admin | Update user role |
| POST | `/users/{id}/assets` | Admin | Assign user to asset |
| DELETE | `/users/{id}/assets/{asset_id}` | Admin | Remove user from asset |

### Notifications

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/notifications` | Authenticated | User's notifications, newest first |
| PUT | `/notifications/{id}/read` | Authenticated | Mark as read |

### Config

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/config` | Admin | Current effective config (merged) |
| PUT | `/config/{key}` | Admin | Set override |
| DELETE | `/config/{key}` | Admin | Remove override (revert to YAML default) |

All list endpoints return cursor-based pagination: `{items: [...], next_cursor: "..."}`.
All obligation/risk detail endpoints inline their evidence objects.
All endpoints that modify data write to `audit_log`.

---

## 9. File Storage

```
/data/
├── originals/
│   └── {document_id}/
│       └── {source_name}          # Immutable. Never modified.
└── processed/
    └── {document_id}/
        └── {source_name}          # OCR overlay PDF (when scanned pages exist)
```

MVP uses local filesystem. The storage layer is abstracted behind an interface (`StorageBackend`) with methods `save(path, data)`, `load(path)`, `exists(path)` so that S3-compatible backends can be swapped in for production.

---

## 10. Testing Strategy

| Tier | Scope | Trigger | LLM | What it validates |
|:----:|-------|---------|:---:|-------------------|
| 1 | Unit | Every commit | Mocked | Quote anchoring, date parsing, modality gating, dedup, scoring weights, normalization, heuristic validators, entity fuzzy matching |
| 2 | Integration | PR merge / nightly | Real | Every obligation has evidence, all quotes findable in page text, confidence in valid range, status matches threshold rules, error isolation works |
| 3 | Golden fixtures | Weekly | Real | Precision and recall against annotated fixture docs (5-10 docs, 1+ per doc_type). Metrics tracked over time |

---

*End of MVP architecture. This document is the implementation reference. SPEC.md contains detailed rationale. CLAUDE.md contains the quick-reference for development.*
