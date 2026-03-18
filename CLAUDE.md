# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Implementation Reference

**MVP_ARCHITECTURE.md** is the single authoritative design reference. **SPEC.md** contains rationale. This file documents what is actually implemented.

## Project Overview

VeritasLayer is an AI Operational Intelligence Layer that ingests PDFs, runs a deterministic 11-stage pipeline, and produces evidence-traceable obligations and risk alerts for operational assets (buildings, construction projects). **Core guarantee: no claim without verifiable evidence** (page number, exact quote, char offsets). Not a chatbot — a truth layer.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy (mapped_column style), Alembic
- **Task Queue:** Celery + Redis
- **Database:** Postgres (no pgvector in MVP)
- **PDF Parsing:** PyMuPDF (`fitz`)
- **OCR:** OLMOCR via DeepInfra (scanned pages only)
- **LLM Routing:** LiteLLM (configured in `backend/config.yaml`)
- **Frontend:** Next.js (App Router, TypeScript) with `@clerk/nextjs`
- **Testing:** pytest + pytest-mock, no DB required for unit tests

## Common Commands

```bash
# Install
pip install -r backend/requirements.txt

# Run tests (Celery not required — stubbed automatically)
python3 -m pytest -q backend/tests
python3 -m pytest backend/tests/test_pipeline_tasks.py::test_parse_document_parses_pdf_pages_and_counts_scanned -v

# Compile check
python3 -m compileall backend/app backend/alembic -q

# Database migrations
python3 -m alembic -c backend/alembic.ini upgrade head
python3 -m alembic -c backend/alembic.ini revision --autogenerate -m "description"
python3 -m alembic -c backend/alembic.ini heads   # current head: e1f2a3b4c5d6

# Dev services
uvicorn backend.app.main:app --reload
celery -A backend.app.worker worker --loglevel=info
```

## Configuration

Config is loaded by `backend/app/config.py` in priority order:

1. `backend/config.yaml` — primary config (committed, dev defaults)
2. DB overrides from `config_overrides` table (TODO, stub in place)
3. Environment variables (secrets only): `DATABASE_URL`, `REDIS_URL`, `DATA_DIR`, `APP_ENV`, `VERITAS_CONFIG_PATH`

OCR env vars (not in YAML): `DEEPINFRA_API_KEY` (required for OCR), `DEEPINFRA_OLMOCR_URL`, `DEEPINFRA_OLMOCR_MODEL`.

LLM env vars (read by LiteLLM, not in YAML): `OPENAI_API_KEY` (for gpt-4o), `ANTHROPIC_API_KEY` (for claude-sonnet), `GEMINI_API_KEY` (for gemini-1.5-pro). At least one must be set for stages 6-7.

Clerk auth env vars: `CLERK_JWKS_URL` (e.g. `https://<domain>.clerk.accounts.dev/.well-known/jwks.json`), `CLERK_ISSUER` (e.g. `https://<domain>.clerk.accounts.dev`). Required for JWT verification in production.

`settings.raw` exposes the full merged YAML dict for nested keys not surfaced on the `Settings` dataclass (e.g., `settings.raw["chunking"]["max_chars"]`).

## Architecture

### Pipeline (11 stages, `backend/app/worker/`)

Orchestrated synchronously in `pipeline.py → process_document`. Each stage calls `update_parse_status()` from `tasks/_helpers.py`, which guards against overwriting a `failed` status.

| Stage | Task function | Status | File |
|---|---|---|---|
| 1 Ingest | `POST /ingest` router | **Implemented** | `routers/ingest.py` |
| 2 Parse | `parse_document` | **Implemented** | `tasks/parse.py` |
| 3 OCR | `ocr_scanned_pages` | **Implemented** | `tasks/ocr.py` |
| 4 Normalize | `normalize_pages` | **Implemented** | `tasks/chunk.py` |
| 5 Chunk | `chunk_pages` | **Implemented** | `tasks/chunk.py` |
| 6 Classify | `classify_document` | **Implemented** | `tasks/classify.py` |
| 7 Extract | `extract_entities/obligations/risks` | **Implemented** | `tasks/extract.py` |
| 8 Verify | `verify_extractions` | **Implemented** | `tasks/verify.py` |
| 9 Score | `score_extractions` | **Implemented** | `tasks/score.py` |
| 10 Persist | `persist_final_status` | **Implemented** | `tasks/notify.py` |
| 11 Notify | `emit_notifications` | **Implemented** | `tasks/notify.py` |

**Scanned page detection:** `len(raw_text.strip()) < 50` → flagged for OCR.

**File storage layout:** originals at `/data/originals/{document_id}/{filename}`, processed at `/data/processed/{document_id}/`.

### Services (`backend/app/services/`)

- `chunking.py` — pure function `split_text_into_chunks(text, max_chars) → list[ChunkSlice]`. Detects section headers (numbered `1. Foo` / `1) Foo` and ALL_CAPS ≥5 chars). Falls back to token-limit splitting.
- `classify.py` (task module) — classification stage creates `extraction_runs`, executes retry/fallback model chain, applies heuristic validation, and sets `documents.doc_type`.
- `extract.py` (task module) — entity/obligation/risk extraction stages create stage-specific `extraction_runs`, process per-chunk outputs, apply stage-level fallback model switching, and persist partial results when chunk failures occur.
- `verify.py` (task module) — deterministic quote verification against normalized page text, evidence creation, rejection on quote mismatch, duplicate suppression, external-reference tagging, and contradiction risk generation.
- `score.py` (task module) — deterministic additive scoring for obligations and risks using config-driven weights/penalties, with `<50 => rejected` and `>=50 => needs_review` gating.
- `llm.py` — LiteLLM wrapper. `classify(model, prompt) → dict`, `extract(model, prompt, stage) → list[dict]`. Internally: `llm_completion()` calls `litellm.completion()` at `temperature=0` with `json_object` format; `parse_json_dict/parse_json_list` validate shape. Raises `LLMResponseError` on empty content or parse failure. Both are patch points in `tasks/classify.py` and `tasks/extract.py`.
- `normalization.py` — NFC + ligature expansion + whitespace collapse.
- `ocr.py` — calls DeepInfra OLMOCR via raw `urllib`. Raises `OCRUnavailableError` on any failure; task layer isolates per-page.
- `storage.py` — `LocalStorage.save(relative_path, bytes)` writes under `data_dir`.

### Models (`backend/app/models/`)

22 domain tables. All models use `UUIDPrimaryKeyMixin` (from `models/base.py`). Key relationships:

- `Document → DocumentPage → TextSpan` (parse output)
- `Document → Chunk` (chunking output)
- `Document → ExtractionRun` (one per stage per run, versioned)
- `Document → Obligation / Risk` → `ObligationEvidence / RiskEvidence` (separate tables, not polymorphic)
- `Obligation → ObligationReview`, `Risk → RiskReview` (separate tables)
- `Obligation ↔ Obligation` via `ObligationContradictions` junction table

All enums are in `models/enums.py`.

### API (`backend/app/routers/`)

- `POST /ingest` — multipart form: `asset_id` (UUID), `uploaded_by` (UUID), `file`. Enforces sha256 dedup, page limit (500), accepts PDF or `.txt`.
- `GET /documents/{id}` and `GET /documents/{id}/status`
- `GET /health`

## Testing Patterns

Celery is **not installed** in the test environment. Tests that import worker tasks must stub it before importing:

```python
if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")
    # ... (see test_pipeline_tasks.py for full stub)
    sys.modules["celery"] = celery_module
```

Pipeline task tests use `FakeSession` + `FakeQuery` (defined in `test_pipeline_tasks.py`, `test_classification_task.py`, `test_extraction_tasks.py`, `test_verify_task.py`, `test_score_task.py`, and `test_notify_task.py`) and inject via `monkeypatch.setattr(task_module, "SessionLocal", lambda: fake_db)`. Tasks import `SessionLocal` at module scope, so monkeypatching the module-level name works.

Service-layer tests (`test_chunking.py`, `test_normalization.py`) have no mocking requirements — pure functions.

`test_llm_service.py` tests `services/llm.py` directly: pure parsing tests need no mocks; integration tests patch `backend.app.services.llm.litellm` (the module-level import) to inject a fake `litellm.completion` return value.

Expected baseline: **54 tests, all passing**.

Latest validation snapshot (2026-03-15):
- `python3 -m pytest -q backend/tests/test_llm_service.py` → `15 passed`
- `python3 -m pytest -q backend/tests` → `54 passed`

## Non-Negotiable Rules

1. **No evidence = no claim.** Every obligation/risk must have evidence with `document_id`, `page_number`, `quote`, char offsets, confidence score, prompt/model version.
2. **Quote-first extraction.** LLM extracts verbatim quotes first, then interprets into structured fields.
3. **No auto-confirm.** All items start as `needs_review` or `rejected`. Only human review sets `confirmed`.
4. **Strict JSON from LLM.** No prose in model outputs.
5. **Rejected items are visible.** All tiers shown: confirmed, needs_review, rejected.
6. **Immutable documents.** Uploaded files never modified. Re-extraction archives old outputs.
7. **Human review is permanent.** `reviewer_confidence` and `field_edits` tracked separately from system values.
8. **Extraction runs are versioned.** Every run records prompt version, model, config snapshot, timestamp in `extraction_runs`.
9. **Store all intermediates.** OCR text, chunks, classification results, raw LLM outputs stored — nothing silently discarded.
10. **Cost controls enforced.** Page limits (500), token limits, concurrency limits (Celery queue config in `config.yaml`).

## Resolved Architectural Decisions

- **No auto-confirm.** Original spec allowed `system_confidence >= 80 → confirmed`. Overridden: human review required.
- **Separate review/evidence tables.** `obligation_reviews`, `risk_reviews`, `obligation_evidence`, `risk_evidence` — proper FKs, not polymorphic.
- **Contradiction junction table.** `obligation_contradictions` with `(obligation_a_id, obligation_b_id)` + link to auto-generated risk.
- **No local OCR fallback.** Scanned pages fail to `partially_processed` if DeepInfra is unavailable.
- **`_helpers.update_parse_status` guards failed state.** Once `failed`, intermediate status updates are no-ops.

## Implementation Status

All 11 pipeline stages implemented. All API routers implemented. Clerk JWT auth live. Next.js frontend scaffolded with Clerk. Postgres running with migrations applied. Current baseline: **54 passing tests**.

**Next:** Frontend review UI — obligations list, risks list, review modal, asset list.

## Frontend Implementation (for Codex)

### Stack
- Next.js 16, App Router, TypeScript, Tailwind CSS v4
- `@clerk/nextjs` already installed and wired (`src/proxy.ts`, `src/app/layout.tsx`)
- Location: `frontend/src/`

### Auth pattern (use on every API call)
```typescript
"use client";
import { useAuth } from "@clerk/nextjs";

const { getToken } = useAuth();
const token = await getToken();
const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/obligations`, {
  headers: { Authorization: `Bearer ${token}` },
});
```

For server components, use `auth()` from `@clerk/nextjs/server` instead.

### Current user
`GET /users/me` returns `{ id, email, name, role }`. Cache this — `id` is required as `reviewer_id` when submitting reviews.

### Build order (P0 first)
1. `src/app/page.tsx` — Asset list (`GET /assets`). Cards showing asset name + pending obligation count.
2. `src/app/obligations/page.tsx` — Obligations table (`GET /obligations?asset_id=...`). Columns: text, type, severity, status, due date. Inline approve/reject buttons.
3. `src/app/risks/page.tsx` — Risks table (`GET /risks?asset_id=...`). Same pattern.
4. Review modal (shared component) — `POST /obligations/{id}/review` or `POST /risks/{id}/review`. Fields: decision (approve/reject/edit_approve), reviewer_confidence (0–100 slider), reason (textarea).

### Key API shapes
See `MVP_ARCHITECTURE.md §6.2` for full request/response shapes.

- **Pagination:** all lists return `{ items, next_cursor }`. Pass `cursor=0` to start.
- **Status colors:** `needs_review` → yellow, `confirmed` → green, `rejected` → red/muted
- **Severity colors:** `critical` → red, `high` → orange, `medium` → yellow, `low` → blue
- **`reviewer_id`** in review POST must be the UUID from `GET /users/me`

### File layout
```
frontend/src/
  app/
    layout.tsx              # ClerkProvider + sticky nav (Assets / Obligations / Risks links)
    page.tsx                # → asset list (implemented)
    obligations/
      page.tsx              # → obligations table
    risks/
      page.tsx              # → risks table
    assets/[id]/documents/
      page.tsx              # → document list + upload dropzone
  components/
    ReviewModal.tsx         # shared approve/reject modal (implemented)
    StatusBadge.tsx         # colored pill for needs_review/confirmed/rejected (implemented)
    SeverityBadge.tsx       # colored pill for low/medium/high/critical (implemented)
  lib/
    api.ts                  # typed fetch helpers (getAssets, getObligations, getRisks, reviewObligation, reviewRisk, ingestDocument, getCurrentUser)
    types.ts                # TypeScript types (Asset, Obligation, Risk, CurrentUser, ReviewPayload, PaginatedResponse)
  proxy.ts                  # clerkMiddleware() — Next.js 16 edge middleware
```

### Frontend env vars (`frontend/.env.local`)
```
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
NEXT_PUBLIC_API_URL=http://localhost:8000   # optional, defaults to localhost:8000
```
