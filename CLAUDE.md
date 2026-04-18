# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Implementation Reference

**This file is the authoritative implementation reference.** `ARCHITECTURE_ORIGINAL_SPEC.md` is the archived original design spec (historical, not maintained). **SPEC.md** contains rationale.

## Project Overview

VeritasLayer is an AI Operational Intelligence Layer that ingests PDFs, runs a deterministic 14-stage pipeline, and produces evidence-traceable obligations and risk alerts for operational assets (buildings, construction projects). **Core guarantee: no claim without verifiable evidence** (page number, exact quote, char offsets). Not a chatbot — a truth layer.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy (mapped_column style), Alembic
- **Job Orchestration:** Inngest (durable step functions — no Celery, no Redis)
- **Database:** Postgres (no pgvector in MVP; `Chunk.embedding` column is JSONB placeholder)
- **PDF Parsing:** PyMuPDF (`fitz`)
- **OCR:** OLMOCR via DeepInfra (scanned pages only)
- **LLM Routing:** LiteLLM (configured in `backend/config.yaml`) — **do not upgrade litellm** (supply chain compromise)
- **Frontend:** Next.js (App Router, TypeScript) with `@clerk/nextjs`
- **Testing:** pytest + pytest-mock, no DB required for unit tests

## Common Commands

The repo has a `Makefile` at the root — prefer `make` targets over raw commands.

```bash
# Install
make install          # installs both backend and frontend deps
pip install -r backend/requirements.txt  # backend only

# Dev servers (each in its own terminal)
make backend          # uvicorn on :8001 (auto-reload)
make frontend         # Next.js on :3000
make inngest          # Inngest dashboard on :8288
make db-up            # start Postgres container
make dev              # backend + frontend together
make dev-all          # db + backend + frontend + inngest

# Run tests
make test                  # backend pytest
make lint                  # frontend ESLint
make build                 # frontend production build
python3 -m pytest -q backend/tests
python3 -m pytest backend/tests/test_pipeline_tasks.py::test_parse_document_parses_pdf_pages_and_counts_scanned -v
cd frontend && npm run test          # Vitest (frontend unit tests)
cd frontend && npm run test:watch    # Vitest in watch mode

# Compile check (run after any edit)
python3 -m compileall backend/app backend/alembic backend/tools -q

# Database migrations
python3 -m alembic -c backend/alembic.ini upgrade head
python3 -m alembic -c backend/alembic.ini revision --autogenerate -m "description"
python3 -m alembic -c backend/alembic.ini heads
# Current head chain: c03dec85f67a → e1f2a3b4c5d6 → a9b8c7d6e5f4 → f3c7beac04b9 → 7c1d4e2b9a10 → b2e4f6a8c0d1 → c4d5e6f7a8b9 → d5e6f7a8b9c0 → e6f7a8b9c0d1 → f7a8b9c0d1e2 (HEAD)

# Eval / benchmark tools (require API key env vars)
python3 -m backend.tools.generate_ground_truth --document-id <uuid>   # AI-labels all obligations/risks
python3 -m backend.tools.evaluate_pipeline --document-id <uuid>       # precision/recall vs ground truth
python3 -m backend.tools.rerun_extraction --document-id <uuid>        # re-run stages 6–10b on existing doc
python3 -m backend.tools.audit_extractions --document-id <uuid>       # LLM-as-judge: adjusted precision for GT under-labeling
python3 -m backend.tools.verify_section_filter --document-id <uuid> --agreement-pages "5-13,43"   # audit section classifier vs known page ranges
python3 -m backend.tools.curate_ground_truth --document-id <uuid> --statutory-pages "1-4,14-42"   # auto-filter GT by page ranges + statutory text
python3 -m backend.tools.rerun_ocr --document-id <uuid>               # re-run OCR on scanned pages only
```

## Docker Deployment

```bash
# Build and start all services (postgres, backend, frontend)
docker compose up --build -d

# Force clean rebuild (required when frontend code changes aren't picked up)
docker compose build --no-cache frontend && docker compose up -d frontend

# Restart backend only (picks up .env.production changes without rebuilding)
docker compose restart backend

# Tail logs
docker compose logs backend --tail=50
docker compose logs frontend --tail=50
```

### Environment files on the server

Two separate files serve different purposes:

- `/app/.env` — read by Docker Compose for **build-time variable substitution** in `docker-compose.yml`. Must contain: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL`, `DB_PASSWORD`, `DROPLET_IP`.
- `/app/.env.production` — passed to containers at **runtime** via `env_file`. Must contain: `DATABASE_URL`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `CORS_EXTRA_ORIGINS`, LLM API keys.

**Critical:** `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` are Docker build args baked into the Next.js bundle at build time. Changing them requires a full frontend rebuild — `docker compose restart` is not enough.

### CORS

Origins allowed by `backend/config.yaml → app.cors_origins`. To add origins without rebuilding, set `CORS_EXTRA_ORIGINS` (comma-separated) in `.env.production` and restart the backend:

```bash
# .env.production
CORS_EXTRA_ORIGINS=https://your-frontend-tunnel.trycloudflare.com
```

## Configuration

Config is loaded by `backend/app/config.py` in priority order:

1. `backend/config.yaml` — primary config (committed, dev defaults)
2. DB overrides from `config_overrides` table (TODO, stub in place)
3. Environment variables (secrets only): `DATABASE_URL`, `DATA_DIR`, `APP_ENV`, `VERITAS_CONFIG_PATH`, `CORS_EXTRA_ORIGINS`

OCR env vars: `DEEPINFRA_API_KEY` (required for OCR), `DEEPINFRA_OLMOCR_URL`, `DEEPINFRA_OLMOCR_MODEL`.

LLM env vars (read by LiteLLM): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`. At least one required for stages 6–10b.

Clerk auth env vars: `CLERK_JWKS_URL`, `CLERK_ISSUER`. Required for JWT verification in production.

`settings.raw` exposes the full merged YAML dict for nested keys (e.g., `settings.raw["chunking"]["max_chars"]`, `settings.raw["rescoring"]`).

**Key config knobs:**
- `llm.chunk_selection.max_chunks_per_stage` — how many chunks the MMR selector sends per extraction stage (default: 10). Set to 0 for all chunks.
- `llm.chunk_selection.mmr_lambda` — MMR diversity/relevance trade-off (0=diverse, 1=relevance-only).
- `rescoring.enabled` — toggle LLM severity re-scoring stage (default: true).
- `rescoring.model` — model for stage 10b (default: `claude-haiku-4-5-20251001`).
- `scoring.weights` / `scoring.penalties` — additive scoring signals, all config-driven.

## Architecture

### Pipeline (14 steps, `backend/app/worker/`)

Orchestrated via Inngest durable step functions in `pipeline.py → process_document`. Each step is a `step.run()` call — Inngest retries failed steps individually. Dashboard at `localhost:8288`.

| Step ID | Task function | File |
|---|---|---|
| 1-parse | `parse_document` | `tasks/parse.py` |
| 2-ocr | `ocr_scanned_pages` | `tasks/ocr.py` |
| 3-normalize | `normalize_pages` | `tasks/chunk.py` |
| 4-chunk | `chunk_pages` | `tasks/chunk.py` |
| 5-classify | `classify_document` | `tasks/classify.py` |
| 5b-section-classify | `classify_chunk_sections` | `tasks/section_classify.py` |
| 6-extract-entities | `extract_entities` | `tasks/extract.py` |
| 7-extract-obligations | `extract_obligations` | `tasks/extract.py` |
| 8-extract-risks | `extract_risks` | `tasks/extract.py` |
| 9-verify | `verify_extractions` | `tasks/verify.py` |
| 10-score | `score_extractions` | `tasks/score.py` |
| 10b-rescore | `rescore_with_llm` | `tasks/rescore.py` |
| 11-persist | `persist_final_status` | `tasks/notify.py` |
| 12-notify | `emit_notifications` | `tasks/notify.py` |

**Scanned page detection:** `len(raw_text.strip()) < 50` → flagged for OCR.

**File storage layout:** originals at `/data/originals/{document_id}/{filename}`, processed at `/data/processed/{document_id}/`.

### Chunk Selection (MMR)

Extraction stages 6–8 use Maximal Marginal Relevance to select the most relevant chunks without redundancy. Each stage has domain keywords in `_STAGE_KEYWORDS` (in `tasks/extract.py`) that drive relevance scoring. The risk extraction keywords include `liquidated`, `indemnif`, `bond`, `insurance` to ensure critical contract clauses score highly. Relevance = 75% keyword hit rate + 25% token richness.

### Services (`backend/app/services/`)

- `llm.py` — LiteLLM wrapper. `llm_completion(model, prompt)` → raw string. Handles list content types, strips code fences (`_strip_code_fences`), and recovers JSON from wrapped text (`_recover_json`). `parse_json_dict` / `parse_json_list` validate shape. Raises `LLMResponseError` on failure. Both are monkeypatch points in task tests.
- `chunking.py` — pure function `split_text_into_chunks(text, max_chars) → list[ChunkSlice]`. Detects section headers (numbered `1. Foo` / `1) Foo` and ALL_CAPS ≥5 chars).
- `normalization.py` — NFC + ligature expansion + whitespace collapse.
- `ocr.py` — calls DeepInfra OLMOCR via raw `urllib`. Raises `OCRUnavailableError`; task layer isolates per-page failures.
- `storage.py` — `LocalStorage.save(relative_path, bytes)` writes under `data_dir`.

### Task modules (`backend/app/worker/tasks/`)

- `classify.py` — creates `extraction_runs`, executes retry/fallback model chain, applies heuristic validation, sets `documents.doc_type`.
- `extract.py` — obligation/risk/entity extraction. MMR chunk selection via `_select_chunks_for_stage`. Per-chunk LLM calls with model fallback. Persists partial results on chunk failure. `_coerce_enum` maps LLM strings to DB enums; obligation type aliases (`delivery→submission`, `maintenance→inspection`, `reporting→compliance`) applied before coercion. Cross-chunk deduplication via `_dedupe_candidates` (containment check, Jaccard overlap, SequenceMatcher ratio) with candidate scoring (`_obligation_candidate_score`, `_risk_candidate_score`) to keep the best match when duplicates span overlapping chunks.
- `verify.py` — deterministic quote verification against normalized page text. Creates evidence records, rejects on quote mismatch, suppresses duplicates, tags external references, generates contradiction risks. Contradiction risks use `RiskType.contractual`.
- `score.py` — deterministic additive scoring. Obligations: 9 signals + 3 penalties. Risks: 7 signals + 2 penalties. `system_confidence < 50 → rejected`. All weights in `config.yaml`.
- `rescore.py` — LLM severity re-scoring (stage 10b). Batches all obligations/risks per document (up to `max_items_per_call`), calls LLM with evidence page context, writes `llm_severity` and `llm_quality_confidence` non-destructively. No-op if `rescoring.enabled=false` or LLM fails.
- `_helpers.py` — `update_parse_status()` guards against overwriting `failed` status.

### Models (`backend/app/models/`)

All models use `UUIDPrimaryKeyMixin` from `models/base.py`. All enums in `models/enums.py`.

Key relationships:
- `Document → DocumentPage → TextSpan` (parse output)
- `Document → Chunk` (chunking output; `embedding` column is JSONB placeholder for future pgvector)
- `Document → ExtractionRun` (one per stage per run; stores `raw_llm_output` as JSONB)
- `Document → Obligation / Risk` → `ObligationEvidence / RiskEvidence`
- `Obligation → ObligationReview`, `Risk → RiskReview`
- `Obligation ↔ Obligation` via `ObligationContradictions` junction table

**LLM re-scoring columns** (nullable, non-destructive) on both `Obligation` and `Risk`:
- `llm_severity: Severity | None` — LLM's revised severity tier
- `llm_quality_confidence: int | None` — LLM's quality confidence 0–100 (check constraint enforced)

**`RiskType` enum values:** `financial | schedule | quality | safety | compliance | contractual | unknown_risk`

**`ParseStatus` values include `rescoring`** (set during stage 10b).

### API (`backend/app/routers/`)

- `POST /ingest` — multipart: `asset_id`, `uploaded_by`, `file`. SHA256 dedup, 500-page limit.
- `GET /documents/{id}`, `GET /documents/{id}/status`, `DELETE /documents/{id}`, `GET /documents/{id}/pdf`, `GET /documents/{id}/pages/{page_number}`, `POST /documents/{id}/process`
- `GET /assets`, `POST /assets`, `GET /assets/{id}`, `DELETE /assets/{id}`, `GET /assets/{id}/documents`
- `GET /obligations`, `GET /obligations/{id}`, `POST /obligations/{id}/review`
- `GET /risks`, `GET /risks/{id}`, `POST /risks/{id}/review` — `decision: approve|reject|edit_approve`, `field_edits` (JSONB), `reviewer_confidence`, `reason`
- `GET /users/me`, `GET /users`, `PUT /users/{id}/role`, `GET /users/{id}/assets`, `POST /users/{id}/assets`, `DELETE /users/{id}/assets/{asset_id}`
- `GET /notifications`, `PUT /notifications/{id}/read`
- `GET /entities`, `GET /entities/suggestions`, `POST /entities/{id}/merge`, `POST /entity-mentions/{id}/resolve`
- `GET /summaries/weekly` (asset_id required), `GET /summaries/weekly/narrative`
- `POST /compliance/reports`, `GET /compliance/reports/{id}`, `GET /compliance/reports`
- `POST /ifc/models`, `GET /ifc/models/{id}`
- `GET /config`, `PUT /config/{key}`, `DELETE /config/{key}` (admin only)
- `GET /health`

### Eval Harness (`backend/tools/`)

CLI scripts for pipeline quality measurement:

- `generate_ground_truth.py` — reads ALL chunks (no MMR limit), calls Claude Sonnet for exhaustive labeling, writes `backend/data/benchmarks/{doc_id}/ground_truth.json`
- `evaluate_pipeline.py` — Jaccard quote matching (threshold 0.6) between ground truth and pipeline output; reports precision, recall, F1, severity exact match, adjacent agreement, Spearman ρ
- `audit_extractions.py` — LLM-as-judge that re-classifies pipeline FPs and GT FNs; reports adjusted precision/recall for GT under-labeling
- `curate_ground_truth.py` — auto-filter GT by page ranges + statutory text (e.g., strip statutory disclosures from eval scope)
- `verify_section_filter.py` — audit section classifier output vs known agreement page ranges
- `rerun_extraction.py` — re-runs stages 6–10b on an existing document (useful after config changes without re-uploading)
- `rerun_ocr.py` — re-run OCR on scanned pages only

Ground truth JSON lives at `backend/data/benchmarks/` — no DB tables needed.

## Testing Patterns

Pipeline task functions are plain Python — no Inngest mocking needed in unit tests.

Task tests use `FakeSession` + `FakeQuery` defined per test file, injected via `monkeypatch.setattr(task_module, "SessionLocal", lambda: fake_db)`. This works because tasks import `SessionLocal` at module scope.

Service-layer tests (`test_chunking.py`, `test_normalization.py`) — pure functions, no mocks.

`test_llm_service.py` patches `backend.app.services.llm.litellm` (the module-level import).

**Current baseline: 198 tests, all passing.**

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
10. **Cost controls enforced.** Page limits (500), token limits, `max_chunks_per_stage` cap in `config.yaml`.

## Resolved Architectural Decisions

- **No auto-confirm.** Original spec allowed `system_confidence >= 80 → confirmed`. Overridden: human review required.
- **Separate review/evidence tables.** `obligation_reviews`, `risk_reviews`, `obligation_evidence`, `risk_evidence` — proper FKs, not polymorphic.
- **Contradiction junction table.** `obligation_contradictions` with `(obligation_a_id, obligation_b_id)` + link to auto-generated risk.
- **No local OCR fallback.** Scanned pages fail to `partially_processed` if DeepInfra is unavailable.
- **`_helpers.update_parse_status` guards failed state.** Once `failed`, intermediate status updates are no-ops.
- **LLM severity stored non-destructively.** `llm_severity` / `llm_quality_confidence` are separate nullable columns; original `severity` / `system_confidence` are never overwritten by stage 10b.
- **RiskType enum uses simple categories.** `financial|schedule|quality|safety|compliance|contractual|unknown_risk` — matches extraction prompt vocabulary. Old granular values (e.g. `payment_term_conflict`) were migrated in `a9b8c7d6e5f4`.

## Implementation Status

All 14 pipeline steps implemented. All API routers implemented. Clerk JWT auth live. Current baseline: **198 passing tests** (backend pytest).

Implemented frontend screens:
- Asset list (`/`) — cards link to `/assets/[id]/documents`
- Obligations table (`/obligations`) — text truncated with `summarizeText()`
- Risks table (`/risks`) — text truncated, rows link to `/risks/[id]`
- Asset document list + upload (`/assets/[id]/documents`)
- Document detail with status polling + tabs (`/documents/[id]`)
- Obligation evidence viewer (`/obligations/[id]`) — formatted quotes, visible surrounding context
- Risk evidence viewer (`/risks/[id]`) — formatted quotes, confidence breakdown scorecard
- Notifications bell dropdown (header overlay)
- Review modal with `edit_approve` field editing (text, severity, risk_type editable inline)
- Status/severity badges — `SeverityBadge` shows `llm_severity` override with visual indicator when present
- Admin user management (`/admin/users`)
- Admin config editor (`/admin/config`)

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
const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"}/obligations`, {
  headers: { Authorization: `Bearer ${token}` },
});
```

For server components, use `auth()` from `@clerk/nextjs/server` instead.

### Current user
`GET /users/me` returns `{ id, email, name, role }`. Cache this — `id` is required as `reviewer_id` when submitting reviews.

### ReviewModal
Accepts `itemType: "obligation" | "risk"` and `initialValues`. When `decision === "edit_approve"`, shows editable fields: `text` (textarea), `severity` (dropdown), and `risk_type` (dropdown, risks only). Only changed fields sent as `field_edits`.

### Key API shapes
See `ARCHITECTURE_ORIGINAL_SPEC.md §6.2` for full request/response shapes (archived historical spec).

- **Pagination:** all lists return `{ items, next_cursor, total }`. `total` is the full count matching the query filters (not just the current page). Pass `cursor=0` to start.
- **Status colors:** `needs_review` → yellow, `confirmed` → green, `rejected` → red/muted
- **Severity colors:** `critical` → red, `high` → orange, `medium` → yellow, `low` → blue
- **LLM severity:** displayed in `SeverityBadge` as override with indicator; falls back to system `severity`
- **`reviewer_id`** in review POST must be UUID from `GET /users/me`

### File layout
```
frontend/src/
  app/
    layout.tsx                    # ClerkProvider + sticky nav
    page.tsx                      # asset list
    obligations/page.tsx          # obligations table (ObligationsClientPage.tsx)
    obligations/[id]/page.tsx     # evidence viewer
    risks/page.tsx                # risks table (RisksClientPage.tsx)
    assets/[id]/documents/page.tsx
    documents/[id]/page.tsx       # status polling + obligations/risks tabs
    admin/users/page.tsx          # user management (UsersClientPage.tsx)
    admin/config/page.tsx         # config editor (ConfigClientPage.tsx)
  components/
    ReviewModal.tsx               # approve/reject/edit_approve modal
    StatusBadge.tsx               # needs_review/confirmed/rejected pill
    SeverityBadge.tsx             # low/medium/high/critical pill with llm_severity support
  lib/
    api.ts                        # typed fetch helpers
    types.ts                      # TypeScript types (includes llm_severity, llm_quality_confidence)
    evidence-utils.ts             # summarizeText, formatQuoteAsProse, buildContextDigest (tested)
    csv.ts                        # downloadCsv, csvFilename helpers
    __tests__/evidence-utils.test.ts
  proxy.ts                        # clerkMiddleware()
```

### Frontend env vars (`frontend/.env.local`)
```
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
NEXT_PUBLIC_API_URL=http://localhost:8001
```

## Human-in-the-Loop Positioning

Human-in-the-loop is a **principled wedge strategy**, not a limitation. It is a trust and liability feature. Apply this reasoning in all product, strategy, and communication decisions.

**Core framing:** For decisions with real financial or legal consequences, a human always makes the call. For everything else, the system helps users move faster. This is a design choice aligned with how experts in high-stakes domains actually work.

**Strengths:** Liability alignment (enterprise buyers cannot auto-approve contract terms), trust moat vs hallucination-prone competitors, regulator-friendly (EU AI Act, emerging US AI regs), augments expert judgment rather than replacing it.

**Risks to watch:**
- **Review bottleneck** — if humans review everything, no time saved. Prioritize ruthlessly; make review UX fast.
- **Rubber-stamp risk** — approval without genuine review creates liability gaps. UX must force real engagement on high-stakes items.
- **Pricing tension** — avoid comparison to cheap full-automation tools; price on value captured.
- **Competitive drift** — as models improve, pure-automation competitors undercut on speed. The trust story must stay sharp.

**Roadmap principle — tiered autonomy:** The architecture already supports this (confidence scores, severity tiers). Low-risk, high-confidence items may eventually auto-approve with audit trails. High-stakes items always require humans. Never frame human-in-the-loop as "forever" — frame it as "always required where it matters."

**Feature evaluation checklist:**
1. Does this make human review faster or slower?
2. Does this risk creating rubber-stamp behavior?
3. Does this preserve the evidence-backed guarantee?
4. Does this leave room for tiered autonomy later?

**Do not:** describe human-in-the-loop as a technical limitation, suggest removing human approval for high-stakes items to compete on speed, or use "full automation" language that contradicts the trust positioning.
