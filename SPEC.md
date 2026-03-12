# SPEC — VeritasLayer: AI Operational Intelligence Layer (MVP)

**Codename:** OpInt (Operational Intelligence)
**Goal:** Convert unstructured project/asset documents into **high-precision**, **evidence-traceable** obligations, deadlines, and risk alerts.

---

## 0) Product Definition

VeritasLayer creates a **truth layer** for operational assets (buildings, construction projects, portfolios, facilities). It ingests documents (PDFs first), extracts structured intelligence (obligations/deadlines/risks), and exposes:

- A REST API to query extracted items
- A review dashboard for "Confirmed," "Needs Review," and "Rejected" items
- Full evidence traceability: every claim is anchored to source text with page + offsets (and bbox where available)
- Deadline monitoring with proactive notifications
- Weekly priority-based summaries (structured data + LLM narrative)

**Not a chatbot MVP.** Chat can be added later on top of the verified truth layer.

---

## 1) MVP Scope

### 1.1 Supported Inputs

- PDF (text-based) — parsed via PyMuPDF
- PDF (scanned) — auto-detected per-page; scanned pages processed via OLMOCR (DeepInfra). OCR output stored with `source="ocr"` and a confidence penalty applied
- Plain text (.txt) for fixtures/tests

### 1.2 Supported Document Types

Doc classification uses LLM + heuristic validation (see §5.1). Each document maps to one of:

- `contract`
- `inspection_report`
- `rfi`
- `change_order`
- `invoice`
- `unknown`

### 1.3 Output Artifacts

1. **Obligations Register** — obligation text, due date (absolute or relative), responsible party, severity, evidence
2. **Risk Alerts** — narrow taxonomy (see §6), severity, evidence, contradiction flags
3. **Weekly Summary** — priority-based digest (structured JSON for dashboard + LLM-generated narrative for export). Focuses on: upcoming deadlines (7/14/30 day windows), high-severity risks, unresolved needs_review items

### 1.4 Document Versioning

Each uploaded document is treated as **independent**. No automatic lineage detection. Same sha256 is deduped; different sha256 is a new document. Users manage which version is current.

---

## 2) Non-Negotiables

### 2.1 Core Product Principle

VeritasLayer is a **verifiable system of record** for obligations, risks, and deadlines extracted from operational documents. It is not a legal advice engine, an automated negotiation tool, or an enterprise analytics platform. These are explicit MVP non-goals.

### 2.2 Evidence Traceability Gate

**No evidence => no claim.**
Every extracted item must include:

- document_id
- page_number
- exact quoted snippet
- char_start/char_end (offset within page text)
- confidence score
- prompt version and model version used for extraction
- (optional but preferred) bbox coordinates for PDF highlighting

### 2.3 Immutable Documents

Uploaded documents are **immutable**. Once ingested, the original file is never modified. New versions of the same logical document create new records, preserving full audit history. Re-extraction creates new output records (old ones archived via audit_log), but never alters the source document.

### 2.4 Human Review Authority

Human reviewers **permanently override** system output. When a reviewer approves, rejects, or edits an item, their decision is final. System-generated values (system_confidence, extracted fields) and human-provided values (reviewer_confidence, edited fields) must always be tracked separately — never merged into a single field.

### 2.5 Extraction Run Versioning

Every processing run is a versioned artifact. Each extraction records:

- prompt_version_id — which prompt template was used
- extraction_model — which LLM model produced the output
- effective configuration snapshot — scoring weights and thresholds at time of extraction
- timestamp

This enables reproducibility: given the same document + prompt version + model + config, the deterministic verification and scoring stages must produce identical results.

### 2.6 Intermediate Artifact Storage

All intermediate pipeline outputs must be stored for debugging and reproducibility:

- OCR text per page (in document_pages with text_source)
- Chunks with split_reason
- Classification result and confidence
- Raw LLM extraction output (logged for debugging)
- Prompt version used at each stage

Intermediate artifacts are never silently discarded. Failed stages store their error in the page/stage processing record.

### 2.7 Quote-First Extraction Policy

LLM extraction MUST be quote-first:

1. Extract verbatim quote spans from the document
2. Interpret them into structured fields referencing those quote spans

Quote verification uses **normalized matching**: both the page text and extracted quote are normalized (collapse whitespace, normalize Unicode, handle ligatures) before comparison. Both raw and normalized offsets are stored — raw offsets for PDF highlighting, normalized text for verification.

If the quote cannot be found after normalization:
- Item cannot be "Confirmed"
- Item becomes "Needs Review" or is stored as "Rejected"

### 2.8 Precision First

Default operational bias:

- Minimize false positives (high precision)
- Accept lower recall initially
- All auto-published items must pass deterministic checks + scoring threshold

---

## 3) System Architecture

### 3.1 Repository Structure

**Monorepo with workspace boundaries:**

```
/backend      — Python (FastAPI + Celery workers)
/frontend     — SvelteKit
/prompts      — Versioned prompt templates
/fixtures     — Golden test fixtures
docker-compose.yml
Makefile / Taskfile
```

### 3.2 Services

| Service | Technology | Role |
|---------|-----------|------|
| **API** | FastAPI | Ingestion, query endpoints, review workflow, auth |
| **Worker** | Celery + Redis | Document processing pipeline (parse, extract, verify, score) |
| **Database** | Postgres | Metadata, structured outputs, config overrides, audit log |
| **Broker** | Redis | Celery task broker + result backend |
| **Object Store** | Local filesystem (MVP), S3-compatible (prod) | Original PDFs, processed PDFs, OCR overlays |
| **Frontend** | SvelteKit | Admin + review dashboard |

**No pgvector in MVP.** Chunks table retains nullable embedding column for future use. Vector search deferred until a concrete use case materializes.

### 3.3 Processing Pipeline

Documents flow through these stages, with **page x stage error isolation** (each page at each stage can independently succeed or fail):

1. **Ingest** — upload file, compute sha256, store original + assign to asset
2. **Parse** — PyMuPDF extracts page text + layout spans with bbox. Per-page: detect if text-based or scanned
3. **OCR** (conditional) — scanned pages sent to OLMOCR via DeepInfra. Detection first, then batch OCR only on scanned pages. OCR text stored with `source="ocr"`
4. **Normalize** — normalize page text (collapse whitespace, Unicode normalization, ligature handling). Store both raw and normalized text
5. **Chunk** — page-based with section splitting. Start with full pages; if a page exceeds token limit, split on section headers or clause boundaries. Detect table-heavy pages and flag them
6. **Classify** — LLM determines doc_type, validated by heuristic rules (e.g., invoices must contain dollar amounts, inspection reports must reference inspections). Disagreement routes to `needs_review`
7. **Extract** — quote-first extraction per chunk. LLM returns strict JSON with verbatim quotes + structured fields
8. **Verify** — deterministic checks: normalized quote match, date parsing, modality check, doc-type constraints, duplicate suppression, contradiction detection
9. **Score** — config-driven confidence scoring (see §7). Dual scoring: system confidence + reviewer confidence
10. **Persist** — store structured outputs + evidence anchors
11. **Notify** — emit events for processing completion, new risks, items awaiting review

### 3.4 LLM Resilience Strategy

Three layers, applied in order:

1. **Retry with exponential backoff** — each LLM call retried up to 3x
2. **Model fallback chain** — if primary model exhausts retries, fall back to secondary model. Fallback happens **per-stage only** (never mix models within a single extraction stage for one document). LiteLLM enables provider switching
3. **Partial results** — if some pages/chunks succeed and others fail after all retries + fallbacks, save successful results. Mark document as `partially_processed` with a list of failed pages/stages

### 3.5 OCR Strategy (OLMOCR via DeepInfra)

- **Detection:** PyMuPDF checks each page for selectable text. Pages with no selectable text (or below a character threshold) are flagged as scanned
- **Batching:** After detection, batch all scanned pages for OCR in parallel. Rate limits managed via Celery task throttling
- **Storage:** OCR output stored in `document_pages` with `text_source="ocr"`. Original page image retained in processed PDF
- **Scoring impact:** OCR-sourced evidence receives a -15 confidence penalty

### 3.6 Cost and Rate Limit Controls

Processing must enforce guardrails to prevent runaway costs:

- **Page limits:** configurable maximum pages per document (default: 500). Documents exceeding the limit are rejected at ingest with a clear error
- **Token limits:** per-chunk token budget for LLM calls. Chunks exceeding the limit are split further or truncated with a warning
- **Concurrency controls:** Celery worker concurrency is configurable. OCR and LLM calls use separate rate-limited task queues to prevent provider throttling
- **Queue-based processing:** all heavy processing runs through Celery queues. The API never blocks on LLM/OCR calls. Queue depth is monitorable for backpressure detection

### 3.7 Document Lifecycle

Documents progress through a defined state machine:

```
uploaded → parsing → ocr (if scanned pages) → chunking → classification → extraction → verification → scoring → complete
```

At any point a document may transition to `failed` or `partially_processed`. The `parse_status` field on documents tracks the high-level state. Individual page x stage status is tracked in `document_pages.processing_status` and the page-level error log.

### 3.8 Table Handling

- **Detection:** Flag pages containing tables using PyMuPDF layout analysis
- **Extraction:** Best-effort text extraction from tables
- **Gating:** If extraction confidence is low on table-heavy pages, auto-route extracted items to `needs_review`

---

## 4) Data Model (Postgres)

### 4.1 Core Tables

#### assets

- id (uuid, pk)
- name (text) — e.g., "Highway 101 Construction", "Building A"
- description (text, nullable)
- created_by (fk users.id)
- created_at (timestamptz)
- updated_at (timestamptz)

#### documents

- id (uuid, pk)
- asset_id (fk assets.id)
- source_name (text)
- file_path (text) — original file
- processed_file_path (text, nullable) — OCR overlay version
- sha256 (text, unique)
- mime_type (text)
- uploaded_by (fk users.id)
- uploaded_at (timestamptz)
- doc_type (enum: contract|inspection_report|rfi|change_order|invoice|unknown)
- doc_type_confidence (float, nullable)
- doc_date (date, nullable)
- parse_status (enum: uploaded|parsing|ocr|chunking|classification|extraction|verification|scoring|complete|partially_processed|failed)
- total_pages (int, nullable)
- scanned_page_count (int, default 0)
- notes (text, nullable)

#### document_pages

- id (uuid, pk)
- document_id (fk documents.id)
- page_number (int)
- raw_text (text) — original extracted text
- normalized_text (text) — whitespace-collapsed, unicode-normalized
- text_source (enum: pdf_text|ocr)
- text_sha256 (text)
- width (float, nullable)
- height (float, nullable)
- has_tables (bool, default false)
- processing_status (enum: pending|processed|failed)
- processing_error (text, nullable)

#### text_spans

- id (uuid, pk)
- document_id (fk)
- page_number (int)
- char_start (int)
- char_end (int)
- bbox_x1, bbox_y1, bbox_x2, bbox_y2 (float)
- span_text (text)
- span_sha256 (text)

#### chunks

- id (uuid, pk)
- document_id (fk)
- page_number (int)
- char_start (int)
- char_end (int)
- text (text)
- embedding (vector, nullable) — reserved for future use
- chunk_sha256 (text)
- split_reason (enum: full_page|section_split|token_limit)
- created_at (timestamptz)

#### entities

- id (uuid, pk)
- canonical_name (text) — globally unique, admin-managed
- entity_type (enum: party|person|org|location|system|other)
- aliases (jsonb) — list of known alternative names
- created_at (timestamptz)
- updated_at (timestamptz)

#### entity_mentions

- id (uuid, pk)
- entity_id (fk entities.id, nullable) — null until resolved
- document_id (fk documents.id)
- mentioned_name (text) — as extracted from document
- page_number (int)
- suggested_entity_id (fk entities.id, nullable) — system's fuzzy match suggestion
- resolved (bool, default false)
- resolved_by (fk users.id, nullable)
- created_at (timestamptz)

#### obligations

- id (uuid, pk)
- document_id (fk)
- obligation_type (enum: compliance|submission|payment|inspection|notification|other)
- obligation_text (text)
- modality (enum: must|shall|required|should|may|unknown)
- responsible_entity_id (fk entities.id, nullable)
- due_kind (enum: absolute|relative|resolved_relative|none)
- due_date (date, nullable)
- due_rule (text, nullable) — e.g., "within 10 days of notice"
- trigger_date (date, nullable) — manually entered for relative deadlines
- severity (enum: low|medium|high|critical)
- status (enum: confirmed|needs_review|rejected)
- system_confidence (int 0-100) — automated score
- reviewer_confidence (int 0-100, nullable) — human assessment
- has_external_reference (bool, default false)
- contradiction_flag (bool, default false)
- contradicts_obligation_id (uuid, nullable)
- extraction_run_id (fk extraction_runs.id, nullable) — links to the run that produced this item
- created_at (timestamptz)
- updated_at (timestamptz)

#### risks

- id (uuid, pk)
- document_id (fk)
- risk_type (enum — see §6)
- risk_text (text)
- severity (enum: low|medium|high|critical)
- status (enum: confirmed|needs_review|rejected)
- system_confidence (int 0-100)
- reviewer_confidence (int 0-100, nullable)
- has_external_reference (bool, default false)
- contradiction_flag (bool, default false)
- extraction_run_id (fk extraction_runs.id, nullable)
- created_at (timestamptz)
- updated_at (timestamptz)

#### obligation_evidence

- id (uuid, pk)
- obligation_id (fk obligations.id)
- document_id (fk)
- page_number (int)
- quote (text)
- quote_sha256 (text)
- raw_char_start (int) — offset in raw page text
- raw_char_end (int)
- normalized_char_start (int) — offset in normalized page text
- normalized_char_end (int)
- bbox_x1, bbox_y1, bbox_x2, bbox_y2 (float, nullable)
- source (enum: pdf_text|ocr)
- created_at (timestamptz)

#### risk_evidence

- id (uuid, pk)
- risk_id (fk risks.id)
- document_id (fk)
- page_number (int)
- quote (text)
- quote_sha256 (text)
- raw_char_start (int)
- raw_char_end (int)
- normalized_char_start (int)
- normalized_char_end (int)
- bbox_x1, bbox_y1, bbox_x2, bbox_y2 (float, nullable)
- source (enum: pdf_text|ocr)
- created_at (timestamptz)

#### reviews

- id (uuid, pk)
- item_type (enum: obligation|risk)
- item_id (uuid)
- decision (enum: approve|reject|edit_approve)
- reviewer_id (fk users.id)
- field_edits (jsonb, nullable) — {"due_date": {"old": "2025-03-15", "new": "2025-03-30"}, ...}
- reviewer_confidence (int 0-100, nullable)
- reason (text, nullable)
- decided_at (timestamptz)

#### users

- id (uuid, pk)
- email (text, unique)
- name (text)
- oidc_provider (text) — "google" or "microsoft"
- oidc_subject (text) — provider's unique user ID
- role (enum: admin|reviewer|viewer)
- is_active (bool, default true)
- created_at (timestamptz)
- last_login_at (timestamptz, nullable)

#### user_asset_assignments

- id (uuid, pk)
- user_id (fk users.id)
- asset_id (fk assets.id)
- created_at (timestamptz)

#### extraction_runs

- id (uuid, pk)
- document_id (fk documents.id)
- prompt_version_id (fk prompt_versions.id)
- model_used (text) — e.g., "gpt-4o", "claude-sonnet-4-20250514"
- config_snapshot (jsonb) — scoring weights and thresholds at time of extraction
- stage (enum: classification|entity_extraction|obligation_extraction|risk_extraction)
- status (enum: running|completed|failed|superseded)
- started_at (timestamptz)
- completed_at (timestamptz, nullable)
- error (text, nullable)
- raw_llm_output (jsonb, nullable) — stored for debugging

#### prompt_versions

- id (uuid, pk)
- prompt_name (text) — e.g., "extract_obligations_contract"
- version (int)
- template (text)
- doc_type (enum, nullable) — null for universal prompts
- description (text, nullable)
- is_active (bool, default false)
- created_by (fk users.id)
- created_at (timestamptz)

#### config_overrides

- id (uuid, pk)
- key (text, unique) — dotted path, e.g., "scoring.quote_match_weight"
- value (jsonb)
- updated_by (fk users.id)
- updated_at (timestamptz)

#### audit_log

- id (uuid, pk)
- table_name (text)
- record_id (uuid)
- action (enum: create|update|delete)
- old_values (jsonb, nullable)
- new_values (jsonb, nullable)
- performed_by (fk users.id, nullable)
- performed_at (timestamptz)

#### notification_events

- id (uuid, pk)
- event_type (enum: processing_complete|deadline_approaching|items_awaiting_review|risk_detected|weekly_summary_ready)
- payload (jsonb)
- created_at (timestamptz)

#### user_notifications

- id (uuid, pk)
- user_id (fk users.id)
- event_id (fk notification_events.id)
- channel (enum: in_app|email)
- status (enum: pending|sent|read)
- sent_at (timestamptz, nullable)
- read_at (timestamptz, nullable)

### 4.2 Invariants

- Evidence quote must match normalized substring of `document_pages.normalized_text` at [normalized_char_start:normalized_char_end]
- `system_confidence` must be present for all obligations/risks
- Status gating rules (§7) must be enforced by the scoring engine
- Every obligation/risk must have at least one evidence record
- `audit_log` must be written for every create/update/delete on obligations, risks, reviews, entities, and config_overrides

### 4.3 File Storage (Three Tiers)

- **Original:** uploaded PDF as-is. Never modified. Source of truth
- **Processed:** PDF with OCR text overlay for scanned pages (when applicable)
- **Extracted text:** stored per-page in document_pages (raw + normalized)

Local filesystem for MVP (`/data/originals/`, `/data/processed/`). S3-compatible interface for production.

---

## 5) Extraction Strategy

### 5.1 Classification (LLM + Heuristic Validation)

1. LLM classifies doc_type from first N pages
2. Heuristic validators confirm:
   - `invoice`: must contain dollar amounts or currency references
   - `inspection_report`: must reference inspection, examination, or assessment
   - `contract`: must contain agreement/party/obligation language
   - `rfi`: must contain request for information / clarification patterns
   - `change_order`: must reference change/modification to existing scope
3. If LLM and heuristics disagree: classify as `unknown` and route to `needs_review`

### 5.2 Extraction Passes

1. **Classify doc_type** (LLM + heuristic validation)
2. **Extract parties/entities** (quote-first where possible). Extracted names are fuzzy-matched against the global entity registry. Matches above threshold become suggestions; new names create unresolved entity_mentions
3. **Extract obligation quotes** (verbatim quotes only)
4. **Interpret obligations** into schema fields referencing evidence spans
5. **Extract risk quotes** (verbatim)
6. **Interpret risks** into schema fields referencing evidence spans
7. **Detect contradictions** — cross-reference obligations within the same document for conflicting terms. Create risk + flag both obligations

### 5.3 Cross-Document References

When an obligation references an external document ("per Exhibit B," "as defined in the Master Agreement"):

- Extract the obligation normally
- Set `has_external_reference = true`
- Force status to `needs_review` regardless of confidence score
- Evidence source noted as partial (anchored in current doc but referencing external)

### 5.4 Model Output Requirements

- Strict JSON only (no prose)
- Each extracted item must include:
  - `quote` (verbatim)
  - `page_number` (int)
  - `explanation` (short) — optional, stored for debugging only
  - Structured fields required by schema
- Each extraction run logs which model and prompt version were used

### 5.5 Deterministic Verifications

- **Normalized quote match** in page text (post-normalization)
- **Modality check:** only "must/shall/required" can auto-confirm
- **Date sanity:**
  - Parseable ISO date for absolute deadlines
  - Relative deadlines: store `due_kind=relative` and `due_rule`
  - Auto-resolve relative dates when triggering event is in the same document (e.g., contract execution date). Otherwise, store as unresolved with user prompt to provide trigger date
- **Doc-type constraints:** invoices should not create "permit required" unless explicitly stated (then `needs_review`)
- **Duplicate suppression:** same quote_sha256 + doc_id + page + offsets => dedupe
- **Contradiction detection:** flag conflicting terms within the same document, create risk of type `payment_term_conflict` or relevant type, flag both obligations

### 5.6 Prompt Registry

Prompts stored as versioned files in `/prompts` directory:

```
/prompts
  /classification
    v1.yaml
    v2.yaml
  /extraction
    /contract
      obligations_v1.yaml
      obligations_v2.yaml
      risks_v1.yaml
    /invoice
      obligations_v1.yaml
  /entity_extraction
    v1.yaml
```

Each prompt file includes: template text, model compatibility notes, description of changes, and the doc_types it applies to. Active version tracked in `prompt_versions` table. Each extraction run logs its prompt version for traceability.

**Selective re-extraction:** admin can trigger re-extraction on specific documents or all documents of a doc_type with a new prompt version. Old results are archived (via audit_log), new results replace them.

---

## 6) Risk Taxonomy (Narrow for Precision)

MVP `risk_type` enum:

- `missing_required_document`
- `expired_certificate_or_insurance`
- `inspection_failed_reinspection_required`
- `approval_overdue`
- `payment_term_conflict`
- `scope_change_indicator`
- `schedule_dependency_blocker`
- `unknown_risk`

Each risk type must have:
- A deterministic trigger rule OR
- A verifier pass requiring explicit supporting quote

Contradictions between obligations automatically generate a risk of the appropriate type, linking both conflicting obligations as evidence.

---

## 7) Confidence Scoring + Gating

### 7.1 Config-Driven Scoring (0–100)

Weights stored in YAML config with DB overrides (via `config_overrides` table):

**Base scores:**

| Feature | Weight |
|---------|--------|
| Quote exact match (normalized) | +40 (required) |
| Strong modality (must/shall/required) | +15 |
| Due date parsed (absolute) OR due_rule present (relative) | +10 |
| Responsible party linked to entity | +10 |
| Doc type aligns with item type | +10 |
| Verifier pass SUPPORTED | +15 |

**Penalties:**

| Condition | Penalty |
|-----------|---------|
| Weak modality (should/may) | -25 |
| OCR source (low confidence) | -15 |
| Contradiction detected | -30 |
| Missing due date when obligation implies deadline | -10 |

### 7.2 Thresholds

- `system_confidence >= 80` AND strong modality => `confirmed`
- `50 <= system_confidence < 80` => `needs_review`
- `system_confidence < 50` => `rejected` (stored and visible in rejected tier)

### 7.3 Dual Scoring

- **system_confidence:** automated score, always computed
- **reviewer_confidence:** set by human during review. Once a reviewer approves (with or without edits), the item becomes `confirmed` regardless of system_confidence. Both scores preserved for analytics

---

## 8) Authentication & Authorization

### 8.1 Identity

OAuth 2.0 / OIDC with Google and Microsoft as identity providers. No local passwords.

### 8.2 Roles

| Role | Permissions |
|------|------------|
| **admin** | Full access. Manage users, roles, assets, config, prompts. Trigger re-extraction |
| **reviewer** | View documents, obligations, risks. Approve/reject/edit items. Cannot manage users or config |
| **viewer** | Read-only access to documents, obligations, risks, summaries |

First user to log in auto-becomes admin. Admin assigns roles in-app after users first log in.

### 8.3 Asset-Scoped Access

Users are assigned to assets via `user_asset_assignments`. Users only see documents/obligations/risks for assets they're assigned to. Admins see all.

---

## 9) Review Workflow

### 9.1 Actions

Reviewers can take three actions on obligations/risks:

1. **Approve as-is** — status becomes `confirmed`, reviewer_confidence recorded
2. **Approve with edits** — reviewer modifies fields (due_date, severity, responsible_party, obligation_text) before approving. All edits stored in `reviews.field_edits` as JSON diff. Edited values applied to the record. Audit trail preserved
3. **Reject** — status becomes `rejected`, reason recorded

### 9.2 Relative Date Resolution

For obligations with `due_kind=relative`:
- If triggering event date can be extracted from the same document (e.g., contract execution date), auto-compute `due_date` and set `due_kind=resolved_relative`
- Otherwise, surface in the review UI with a prompt for the reviewer to input the trigger date. On input, compute `due_date`, set `trigger_date`, and update `due_kind=resolved_relative`

---

## 10) Entity Resolution

### 10.1 Global Registry

Entities are maintained in a global registry (not scoped per-document). Each entity has a `canonical_name` and an `aliases` list.

### 10.2 Extraction Flow

1. LLM extracts party/entity names from documents
2. Each extracted name is fuzzy-matched against existing entities
3. If match confidence is high, system creates an `entity_mention` with a `suggested_entity_id`
4. Reviewer confirms or overrides the suggestion
5. If no match, a new `entity_mention` is created as unresolved. Reviewer can create a new entity or assign to existing

### 10.3 Merge Suggestions

System surfaces merge suggestions when entity names across documents are similar. Reviewer must confirm merges manually. No auto-merge.

---

## 11) Notifications & Events

### 11.1 Event Types

| Event | Trigger | Recipients |
|-------|---------|------------|
| `processing_complete` | Document finishes processing | Uploader + asset-assigned users |
| `deadline_approaching` | Obligation due date within 7/1 day(s) | Asset-assigned users |
| `items_awaiting_review` | Daily digest | Reviewers assigned to asset |
| `risk_detected` | New high/critical severity risk | Asset-assigned users |
| `weekly_summary_ready` | Summary generation completes | Asset-assigned users |

### 11.2 Channels

- **In-app:** notification bell in the dashboard
- **Email:** configurable per-user

### 11.3 Architecture

Internal event bus with clean interface designed for future webhook support. Events stored in `notification_events` table. Webhook endpoints can be added without refactoring the event emission layer.

---

## 12) Frontend (SvelteKit)

### 12.1 Scope (MVP)

**Admin + review hybrid dashboard:**

- Document upload with processing status (polling-based progress: stage + percentage)
- Document list with filters (asset, doc_type, status)
- Obligation/risk tables with filters (status, severity, document, asset)
- Review interface: approve / approve-with-edits / reject — inline within the table
- Evidence viewer: **PDF primary with bbox highlights** (pdf.js), falling back to text-with-highlights when bbox data is unavailable. Side panel shows quote in context
- Entity management: view global registry, resolve suggestions, merge entities
- Basic deadline view (upcoming obligations sorted by due date)
- Notification bell with in-app notifications
- User role display (admin sees user management)

**Not in MVP frontend:** calendar view, advanced analytics, webhook management UI (admin via API/CLI).

### 12.2 Communication

SvelteKit frontend communicates with FastAPI backend via REST API. Authentication via OIDC tokens passed as Bearer headers. Polling endpoint for document processing progress.

---

## 13) Configuration

### 13.1 Layered Config

1. **YAML defaults** (version controlled in repo): scoring weights, thresholds, model preferences, notification rules
2. **Database overrides** (`config_overrides` table): admin-editable at runtime without redeploy
3. **Environment variables**: secrets only (database URL, API keys, OIDC client secrets)

DB overrides take precedence over YAML defaults. Env vars for secrets only.

### 13.2 LLM Configuration

```yaml
llm:
  primary_model: "gpt-4o"
  fallback_models:
    - "claude-sonnet-4-20250514"
    - "gemini-1.5-pro"
  max_retries: 3
  retry_backoff_base: 2  # seconds
  provider: "litellm"
```

**Configurable per deployment:** cloud LLM APIs, enterprise zero-data-retention tiers, or self-hosted models (via vLLM/Ollama). LiteLLM abstracts the provider. Customer chooses based on their compliance needs.

---

## 14) API (FastAPI)

### 14.1 Endpoints

**Documents:**
- `POST /ingest` (multipart file upload, asset_id required) -> document_id
- `GET /documents/{id}` -> metadata + processing status + progress
- `GET /documents/{id}/status` -> processing stage + percentage (for polling)
- `GET /documents/{id}/pages/{page_number}` -> page text + spans
- `POST /documents/{id}/reextract` -> trigger re-extraction with specified prompt version (admin only)

**Obligations & Risks:**
- `GET /obligations?status=...&severity=...&document_id=...&asset_id=...`
- `GET /risks?status=...&severity=...&document_id=...&asset_id=...`
- `GET /obligations/{id}` -> full obligation with evidence inline
- `GET /risks/{id}` -> full risk with evidence inline

**Review:**
- `POST /review` -> approve / reject / edit+approve an item

**Entities:**
- `GET /entities` -> global entity registry
- `GET /entities/suggestions` -> pending merge suggestions
- `POST /entities/{id}/merge` -> merge two entities (reviewer+)
- `POST /entity-mentions/{id}/resolve` -> assign mention to entity

**Summaries:**
- `GET /summary/weekly?asset_id=...` -> structured JSON digest
- `GET /summary/weekly/narrative?asset_id=...` -> LLM-generated narrative

**Assets:**
- `GET /assets` -> list assets
- `POST /assets` -> create asset (admin)
- `GET /assets/{id}` -> asset details with document count

**Users & Auth:**
- `GET /auth/login/{provider}` -> initiate OIDC flow
- `GET /auth/callback` -> OIDC callback
- `GET /users/me` -> current user
- `GET /users` -> list users (admin)
- `PUT /users/{id}/role` -> update role (admin)
- `POST /users/{id}/assets` -> assign user to asset (admin)

**Notifications:**
- `GET /notifications` -> user's notifications
- `PUT /notifications/{id}/read` -> mark as read

**Config:**
- `GET /config` -> current effective config (admin)
- `PUT /config/{key}` -> set config override (admin)

### 14.2 Response Format

API responses for obligations/risks must include evidence objects inline. Pagination via cursor-based pagination for list endpoints.

---

## 15) Testing Strategy

### 15.1 Three-Tier Testing

**Tier 1: Unit Tests (mocked LLM)**
- Quote anchoring: verify normalized_text[char_start:char_end] equals quote
- Date parsing: absolute date parse + relative rule storage
- Modality gating: "should/may" cannot confirm
- Dedup: duplicate evidence prevented
- Confidence scoring: weight calculations, threshold gating
- Normalization: Unicode, whitespace, ligature handling
- Heuristic classification validators
- Entity fuzzy matching logic
- LLM responses mocked with fixture JSON

**Tier 2: Integration Tests (real LLM, assert properties)**
- Every extracted obligation has at least one evidence record
- All quotes are findable in normalized page text
- Confidence scores are within valid range (0-100)
- Status values match threshold rules
- No obligation exists without evidence
- Pipeline error isolation works (failed pages don't block others)

**Tier 3: Golden Fixture Suite (weekly, full evaluation)**
- 5-10 fixture docs (txt + simple PDFs), at least 1 per doc_type
- Expected outputs defined: obligations, risks, entities
- Includes ambiguous language edge cases
- Includes relative deadline examples
- Includes cross-reference examples
- Measures precision and recall against golden annotations
- Runs with real LLM calls; metrics tracked over time

### 15.2 CI Requirements

- Tier 1 runs on every commit
- Tier 2 runs on PR merge (or nightly)
- Tier 3 runs weekly with results published

---

## 16) Deployment

### 16.1 Development

Docker Compose orchestrating: FastAPI, Celery worker, Redis, Postgres, SvelteKit dev server.

```yaml
services:
  api:       # FastAPI (uvicorn)
  worker:    # Celery worker
  redis:     # Task broker
  postgres:  # Database
  frontend:  # SvelteKit dev server
```

### 16.2 Production

Cloud-native deployment (specifics decided later). Architecture supports:

- Managed Postgres (RDS / Cloud SQL)
- Container orchestration (Cloud Run / ECS / K8s)
- S3-compatible object storage for documents
- Managed Redis (ElastiCache / Memorystore) for Celery broker

Dockerfiles provided for API, worker, and frontend.

---

## 17) Definition of Done (MVP)

- Upload PDF to an asset -> system parses pages, runs OCR on scanned pages, extracts obligations/risks
- Items show in dashboard with:
  - System confidence score
  - Status (confirmed / needs_review / rejected)
  - Evidence anchors with PDF highlighting (bbox) or text fallback
- Reviewers can approve, reject, or edit+approve items with audit trail
- Entity resolution with global registry and manual merge
- Relative deadlines resolvable via reviewer input or auto-computation
- Contradictions detected and surfaced as risks
- Asset-scoped notifications for deadlines, processing, and risks
- Weekly priority-based summary (structured + narrative)
- Config-driven scoring weights editable without redeploy
- All three test tiers passing
- Docker Compose for local development
- OAuth login with Google and Microsoft
