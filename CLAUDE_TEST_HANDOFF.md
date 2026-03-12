# CLAUDE_TEST_HANDOFF.md

## Purpose
This document is an implementation handoff for independent validation of current MVP progress.

## Implemented Scope (Current)

### 1) Project scaffolding + API baseline
- FastAPI app with health endpoint:
  - `GET /health`
- Ingest endpoint:
  - `POST /ingest` (accepts PDF or `.txt`, enforces dedup by sha256, validates PDF page limit, stores source under `/data/originals/{document_id}/...`, dispatches Celery pipeline)
- Document status endpoints:
  - `GET /documents/{id}`
  - `GET /documents/{id}/status`

Primary files:
- `backend/app/main.py`
- `backend/app/routers/ingest.py`
- `backend/app/routers/documents.py`

### 2) Database models + migration baseline
- SQLAlchemy models defined for MVP schema (22 tables).
- Alembic configured and initial migration created.

Primary files:
- `backend/app/models/*.py`
- `backend/alembic.ini`
- `backend/alembic/env.py`
- `backend/alembic/versions/c03dec85f67a_initial_schema.py`

### 3) Pipeline Stages 2-11 (implemented)
Implemented task logic:
- `parse_document`
  - Parses PDF pages with PyMuPDF
  - Creates `document_pages`
  - Extracts `text_spans`
  - Detects scanned pages (`len(raw_text.strip()) < 50`)
  - Handles `.txt` as single-page input
  - Per-page failure isolation (`processing_status=failed`, error captured)
- `ocr_scanned_pages`
  - Attempts OCR only on scanned pages
  - Per-page OCR failure isolation
  - Updates `text_source=ocr` when OCR succeeds
  - Writes a processed file path artifact under `/data/processed/{document_id}/...`
  - Note: current processed artifact is a placeholder copy, not true OCR overlay rendering yet
- `normalize_pages`
  - Normalizes whitespace + NFC + ligature expansion
  - Updates `normalized_text`, `text_sha256`, and page status
- `chunk_pages`
  - Splits normalized text by section boundaries when possible
  - Falls back to token-limit splitting
  - Persists chunk offsets and split reason
- `classify_document`
  - Creates `extraction_runs` for stage `classification`
  - Uses retry + fallback model chain from config
  - Applies heuristic validation against extracted page text
  - Sets `documents.doc_type` / `doc_type_confidence` on agreement; sets `unknown` on disagreement/failure
- `extract_entities` / `extract_obligations` / `extract_risks`
  - Creates stage-specific `extraction_runs`
  - Processes document chunks with per-chunk failure isolation
  - Uses stage-level fallback model switching (no switching back to primary)
  - Persists successful partial results even when some chunks fail
  - Entity mentions include fuzzy `suggested_entity_id` matching
  - Obligations/risks created with `status=needs_review` and `system_confidence=0` (scoring stage pending)
- `verify_extractions`
  - Deterministically normalizes and verifies quote presence in `document_pages.normalized_text`
  - Creates `obligation_evidence` / `risk_evidence` with offsets and source text type
  - Rejects items when quote cannot be anchored
  - Applies duplicate suppression on quote hash + position key
  - Flags external references (`per Exhibit`, `pursuant to`, etc.)
  - Detects obligation contradictions and creates `payment_term_conflict` risk + contradiction junction rows
- `score_extractions`
  - Computes config-driven additive `system_confidence` for obligations and risks
  - Applies penalties for weak modality, OCR evidence, contradictions, and missing deadline
  - Applies status gating (`<50 => rejected`, `>=50 => needs_review`)
- `persist_final_status`
  - Sets `complete` when no failed pages
  - Sets `partially_processed` when any page failed
- `emit_notifications`
  - Emits `processing_complete` for each processed document
  - Fans out recipients to uploader + all asset-assigned users (deduplicated)
  - Creates `user_notifications` per recipient/channel (`in_app`, plus `email` when `notifications.email_enabled=true`)
  - Emits `risk_detected` when at least one risk has severity in (`high`, `critical`)

Primary files:
- `backend/app/worker/tasks/parse.py`
- `backend/app/worker/tasks/ocr.py`
- `backend/app/worker/tasks/chunk.py`
- `backend/app/worker/tasks/classify.py`
- `backend/app/worker/tasks/extract.py`
- `backend/app/worker/tasks/verify.py`
- `backend/app/worker/tasks/score.py`
- `backend/app/worker/tasks/notify.py`
- `backend/app/services/llm.py`
- `backend/app/services/normalization.py`
- `backend/app/services/chunking.py`
- `backend/app/services/ocr.py`
- `backend/app/worker/tasks/_helpers.py`

### 4) TDD test baseline added
Unit tests are in place for deterministic behavior and stage task logic.

Test files:
- `backend/tests/test_normalization.py`
- `backend/tests/test_chunking.py`
- `backend/tests/test_ocr_service.py`
- `backend/tests/test_pipeline_tasks.py`
- `backend/tests/test_classification_task.py`
- `backend/tests/test_extraction_tasks.py`
- `backend/tests/test_verify_task.py`
- `backend/tests/test_score_task.py`
- `backend/tests/test_notify_task.py`
- `backend/tests/test_llm_service.py`

Observed expected result at handoff time:
- `42 passed`

Latest local validation (2026-03-12):
- `python3 -m pytest -q backend/tests/test_llm_service.py` → `15 passed`
- `python3 -m pytest -q backend/tests` → `42 passed`

### 5) Important fix discovered via TDD
- Fixed ambiguous ORM relationship in `Entity.mentions` by specifying `foreign_keys`.

File:
- `backend/app/models/entity.py`

## Not Implemented Yet (Expected)
- OCR overlay PDF generation (currently placeholder copy)

## Independent Validation Checklist

### A) Static sanity checks
From repo root:
```bash
python3 -m compileall backend/app backend/alembic
```
Expected: no compile errors.

### B) Run unit tests
From repo root:
```bash
python3 -m pytest -q backend/tests
```
Expected: all tests pass (currently 42 tests).

### C) Migration checks
From repo root:
```bash
python3 -m alembic -c backend/alembic.ini heads
```
Expected head:
- `c03dec85f67a`

Optional SQL render check (no DB writes):
```bash
DATABASE_URL=postgresql+psycopg2://veritas:veritas@localhost:5432/veritas \
python3 -m alembic -c backend/alembic.ini upgrade head --sql > /tmp/veritas_initial_schema.sql
```
Expected: SQL renders successfully and includes enum/type/table creation.

## Runtime Notes for Claude
- `POST /ingest` currently requires form fields `asset_id`, `uploaded_by`, and `file`.
- Worker pipeline is orchestrated in `backend/app/worker/pipeline.py` and runs through Stage 11 (`emit_notifications`).
- Status progression guard exists: once a document is `failed`, intermediate stage updates do not overwrite it (`update_parse_status`).

## Recommended Next Implementation Target
Use TDD for API-layer delivery:
1. Implement routers for obligations, risks, evidence, review actions, and asset queries.
2. Add route-level tests (filters, pagination, 404 behavior, state transitions, and audit writes).
3. Add OCR overlay PDF generation (replace placeholder processed artifact) after API coverage is in place.
