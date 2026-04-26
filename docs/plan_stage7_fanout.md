# Plan: Stage 7 fan-out refactor

**Status:** Proposed
**Owner:** —
**Estimated effort:** ~1 day (code) + ~0.5 day (tests, deploy, monitor)

## Problem

`step.run("7-extract-classify", lambda: extract_obligations_and_risks(document_id))` in `backend/app/worker/pipeline.py:43` runs synchronously for ~7 minutes on a 22-page document with `max_chunks_per_stage=0`. The Inngest → backend HTTP request goes through Cloudflare (100 s proxy timeout), which 524s long before the function returns. Inngest treats the step as failed, retries up to 2× (`retries=2`), each retry runs the full ~7 minutes again, and after 3 attempts the pipeline is stuck at `parse_status: extraction`. Server-side the LLM calls all succeed — the duplicates bloat the DB (3× obligations/risks) and confuse downstream verification.

Witnessed on `c01494df-95b5-40de-9627-90a650dc7b9a` (HO3_sample.pdf): 3 obligation_extraction runs + 3 risk_extraction runs, all `completed` with `errors=0`, but `parse_status` never advanced past `extraction`.

## Goal

Every `step.run()` invocation completes in **< 90 s** (safe margin under Cloudflare's 100 s) regardless of document size, with stage 7's total wall-clock time *reduced* (not just split), via parallel per-group LLM calls.

## Non-goals

- Removing Cloudflare proxy in front of the API. (Separate ops decision; this plan assumes the proxy stays.)
- Changing the dedup, scoring, or critic logic.
- Touching stages 1–6, 9, 9a, 10, 10b, 11, 12.

## High-level design

Replace the single long step with three Inngest stages plus a parallel fan-out:

```
7a-plan        → 1 short DB step.       (<5 s)
7b-extract-*   → N parallel LLM steps.  (each ~30 s, run via step.parallel)
7c-persist     → 1 short DB step.       (<10 s)
```

Where N = `ceil(num_agreement_chunks / chunks_per_group) * 2` (one set per sub-stage: obligations + risks).

## Detailed design

### 1. New table: `extraction_group_jobs`

Persists the planned work and per-group results so retries are deterministic and resumable.

```sql
CREATE TABLE extraction_group_jobs (
    id UUID PRIMARY KEY,
    extraction_run_id UUID NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,                  -- "obligation_extraction" | "risk_extraction"
    group_index INT NOT NULL,
    chunk_ids UUID[] NOT NULL,                 -- the chunks in this group
    status TEXT NOT NULL,                      -- "pending" | "running" | "completed" | "failed"
    model_used TEXT,
    response JSONB,                            -- raw LLM list response
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (extraction_run_id, group_index)
);
CREATE INDEX ix_extraction_group_jobs_run ON extraction_group_jobs(extraction_run_id);
```

Alembic migration: new file under `backend/alembic/versions/`. Forward-only; old data unaffected.

### 2. New tasks (in `backend/app/worker/tasks/extract.py`)

#### `plan_stage_7(document_id) -> dict`
- Loads document + chunks.
- Runs section filter + chunk grouping (existing `_select_chunks_with_section_filter_guardrails` and `_group_chunks`).
- Creates two `ExtractionRun` rows (one for obligations, one for risks).
- Inserts N `extraction_group_jobs` rows (status=`pending`).
- Returns a serializable plan: `{"ob_run_id": …, "ri_run_id": …, "ob_group_count": …, "ri_group_count": …, "ob_job_ids": [...], "ri_job_ids": [...]}`.

#### `run_extraction_group(job_id) -> dict`
- Loads one `extraction_group_jobs` row.
- Builds prompt via `_build_grouped_extraction_prompt`.
- Calls `call_extract_llm` with model fallback chain.
- Writes `response`, `model_used`, `status`, timestamps back to the row.
- Returns `{"job_id": ..., "status": ..., "item_count": ...}`.

#### `persist_stage_7(document_id, ob_run_id, ri_run_id) -> dict`
- Loads completed group rows for both runs.
- Reuses `_dedupe_candidates`, `_resolve_party_entity_id`, etc.
- Writes `Obligation` / `Risk` rows.
- Calls `_finish_run` for both extraction runs.

### 3. Pipeline orchestration

`backend/app/worker/pipeline.py:43` becomes:

```python
plan = await step.run("7a-plan", lambda: plan_stage_7(document_id))

ob_steps = [
    (f"7b-ob-{i:03d}", lambda jid=jid: run_extraction_group(jid))
    for i, jid in enumerate(plan["ob_job_ids"])
]
ri_steps = [
    (f"7b-ri-{i:03d}", lambda jid=jid: run_extraction_group(jid))
    for i, jid in enumerate(plan["ri_job_ids"])
]
await step.parallel([*ob_steps, *ri_steps])

await step.run("7c-persist", lambda: persist_stage_7(document_id, plan["ob_run_id"], plan["ri_run_id"]))
```

Step IDs are deterministic (zero-padded indices over the `ob_job_ids` list). Inngest will memoize per-group results, so a retry only re-runs failed groups.

### 4. Backward compatibility for CLI tools

`backend/tools/rerun_extraction.py`, `generate_ground_truth.py`, etc. call `extract_obligations_and_risks` synchronously. Keep that function as a thin wrapper:

```python
def extract_obligations_and_risks(document_id):
    plan = plan_stage_7(document_id)
    for jid in [*plan["ob_job_ids"], *plan["ri_job_ids"]]:
        run_extraction_group(jid)
    return persist_stage_7(document_id, plan["ob_run_id"], plan["ri_run_id"])
```

Existing tests of the synchronous path keep passing with no changes.

### 5. Concurrency and rate-limiting

`step.parallel` runs all children concurrently. For a 22-page doc that's ~22 simultaneous Sonnet calls. Risks: Anthropic per-minute token rate limit, LiteLLM concurrency. Mitigations:

- Add a `llm.max_concurrent_requests` config knob and chunk `step.parallel` into batches of that size (use sequential `await step.parallel(batch)` calls).
- Default value: 5 (safe with current Anthropic tier).
- Document tradeoff: lower value → more sequential time, higher value → faster but risk of 429s.

### 6. Failure handling

- A failing group sets `status=failed` and records `error`. Inngest's per-step retry (Inngest function-level `retries=2`) re-invokes `run_extraction_group(jid)` automatically — and because the row is already there with `status=running`, retries are idempotent.
- `persist_stage_7` skips groups in non-`completed` status and includes them in the result summary as `failed_group_count`. The run status is `failed` if any group failed AND no successful items, else `completed` with partial coverage.

## Tests

- `test_extract_plan_stage_7.py` — verifies grouping, run creation, job rows match selected chunks.
- `test_extract_run_extraction_group.py` — patches `call_extract_llm`, asserts row fields are populated and idempotent on re-run.
- `test_extract_persist_stage_7.py` — feeds canned per-group rows, asserts dedup and persistence parity with old behavior.
- `test_extract_legacy_wrapper.py` — runs `extract_obligations_and_risks` end-to-end via the new wrapper, asserts current behavior preserved.
- Integration smoke: re-run benchmarks with `python3 -m backend.tools.evaluate_pipeline --document-id <lease-benchmark>` after deploy; F1 should be ≥ current baseline.

## Migration / rollout

1. PR 1: Alembic migration adds `extraction_group_jobs` table. Deploy; nothing reads or writes it yet.
2. PR 2: New tasks + wrapper + tests. Pipeline.py NOT yet changed — old single-step path still runs. Verify wrapper produces identical output on benchmark doc.
3. PR 3: Switch `pipeline.py` to fan-out. Deploy off-peak. Monitor first 5 documents through stage 7 for completeness and step duration.
4. Rollback: revert PR 3 only — wrapper keeps the synchronous code path working.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Inngest step.parallel cap | Low | Batch in `max_concurrent_requests`-sized waves. |
| Anthropic 429 under fan-out | Medium | Concurrency cap + existing retry-with-backoff. |
| Determinism of step IDs across retries | Medium | Plan persisted in `extraction_group_jobs` before any LLM call. Step IDs derived from job_ids loaded by 7a. |
| Cloudflare still 524s on 7a / 7c | Low | Both are ≤ a couple seconds (DB-only). Verify in staging. |
| Hidden coupling in `_extract_obligations_impl` | Medium | Read once carefully before refactoring. Keep dedup/scoring untouched. |

## Open questions

- Should `extraction_group_jobs` be cleaned up after `7c-persist`, or kept for audit/debugging? Recommend keep — small footprint, valuable for incident review.
- Do we need a separate `classify_extraction` job kind for the existing `_extract_classified_impl` path? It's only used when full-doc mode is enabled, which is currently disabled in config. Defer until full-doc returns.
- Cap on `chunks_per_group`? Today: 5. If a single group still ever takes > 90 s on a pathological chunk, lower to 3.

## Done definition

- New tables + tasks in place behind feature flag (or simple deploy ordering).
- All existing extraction tests pass.
- Benchmark doc `3c429f92` produces F1 within ±0.02 of current baseline.
- A 22+ page document completes the entire 14-stage pipeline end-to-end without Inngest retries.
- Per-step durations in Inngest dashboard all < 90 s.
