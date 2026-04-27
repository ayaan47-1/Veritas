# Problem Statement: Stage 9a (critic) timeouts

**Status:** Open — investigating options
**First observed:** 2026-04-26 (HO3_sample.pdf, doc `c01494df-95b5-40de-9627-90a650dc7b9a`)
**Severity:** Medium — does not block pipeline completion, but degrades final quality and produces `partially_processed` documents.

## Symptom

When a document reaches stage 9a (`criticize_extractions` in `backend/app/worker/tasks/critic.py`), the LLM call sometimes raises:

```
litellm.Timeout: AnthropicException - litellm.Timeout: Connection timed out after 120.0 seconds
```

The extraction run row is marked `failed` with `outputs=0` (no items were judged). `persist_final_status` then sets `parse_status = partially_processed` because at least one extraction run failed.

## Evidence

From `diagnose_document --document-id c01494df-95b5-40de-9627-90a650dc7b9a --errors` on 2026-04-27:

```
ExtractionStage.critic_detection | failed | started=2026-04-27 02:50:36 | model=claude-sonnet-4-6 | outputs=0 | err=litellm.Timeout: Connection timed out after 120.0 seconds
ExtractionStage.critic_detection | failed | started=2026-04-27 00:07:41 | model=claude-sonnet-4-6 | outputs=0 | err=litellm.Timeout: Connection timed out after 120.0 seconds
```

Both failures: same model, same timeout duration, `outputs=0` (failed before any item was judged), no partial progress. Other extraction stages on the same document (entity, obligation, risk, classification) all succeeded with `errors=0` using the same model.

## Current configuration (`backend/config.yaml`)

```yaml
critic:
  enabled: true
  model: "claude-sonnet-4-6"
  max_items_per_call: 30
  auto_reject_threshold: 70
```

LLM client timeout (`backend/app/services/llm.py:38`):
```python
def llm_completion(model, prompt, *, prefer_json_object=True, timeout=120):
```

## What stage 9a does

Per CLAUDE.md and `tasks/critic.py`:
- Loads all obligations + risks for the document.
- Batches them in groups of `max_items_per_call` (30).
- For each batch, builds a prompt that includes the items plus `full_text` of the document and asks the model to score each item's quality and re-classify if needed.
- Items judged below `auto_reject_threshold` (70) are auto-rejected.

For the HO3 doc: 147 obligations + 165 risks = 312 items → ~11 batches per critic run.
The 120 s timeout is per-batch (per LLM call), not per-document.

## Impact

- **User-facing:** documents land in `partially_processed` instead of `complete` whenever any critic batch times out. UI shows the partial state.
- **Quality:** items in failed batches keep their original system_confidence and never get critic adjustment, so low-quality extractions don't get auto-rejected.
- **Cost:** each timeout still bills for the full prompt input (Anthropic does not refund timeouts). With 11 batches per doc and intermittent failures, wasted spend is real but bounded.
- **Recurrence:** likely affects every document large enough to need multiple batches (≥30 items), which on this codebase is essentially every multi-page contract/policy.

## Constraints

- Critic runs synchronously inside an Inngest step — same Cloudflare 100 s ceiling applies to *the whole stage*, not per-batch (see `docs/plan_stage7_fanout.md`). This bounds how long stage 9a can run end-to-end before Inngest itself starts retrying.
- Cannot upgrade `litellm` (compromised package — see memory `feedback_litellm_security.md`).
- Must preserve the "no claim without evidence" guarantee — anything that reduces critic coverage needs to be a deliberate choice, not a side effect.
- Anthropic API key is shared across stages (single org-level rate limit).

## What we don't know yet

These are the questions to research before picking an option:

1. **Is the timeout time-to-first-token, or total response time?** LiteLLM's `timeout` parameter behavior under streaming vs non-streaming is unclear from our usage. If it's TTFT, the issue is provider-side queueing, not slow generation — different remedy.
2. **What's the typical batch latency on Sonnet 4.6 for a 30-item critic prompt?** We don't have telemetry. Could be 30 s typical with rare 120 s spikes (transient), or could be 100 s typical with frequent timeouts (chronic).
3. **How does Haiku 4.5 compare on this exact prompt for accuracy?** Rescore (10b) already uses Haiku 4.5 successfully. But critic does more than severity rescoring — it judges quality and can re-classify. Quality parity not measured.
4. **Are the timeouts correlated with time of day / Anthropic load?** Both observed failures were within ~2.5 hr of each other (00:07 and 02:50 UTC) — possibly an Anthropic capacity dip, not a pattern.
5. **Does critic have model fallback?** Extract stages fall back Sonnet → Haiku on timeout. Critic likely doesn't (need to verify in `tasks/critic.py`). If it doesn't, adding fallback alone might fix it without changing the primary model.
6. **How big is the prompt actually?** Includes full document text per CLAUDE.md ("includes evidence pages in the prompt"). For a 22-page doc that's ~30 K tokens of context per batch, sent 11 times. Could be optimized to send only the *cited pages* per item.

## Option space (not yet decided)

| # | Option | Effort | Risk | Addresses |
|---|---|---|---|---|
| 1 | Switch `critic.model` to `claude-haiku-4-5-20251001` | 1 line | Low (matches rescore precedent) | Speed only |
| 2 | Bump `llm_completion(timeout=)` from 120 s → 300 s | 1 line, but global | Medium (affects all stages; could mask other slow paths) | Tolerance only |
| 3 | Add Sonnet → Haiku fallback chain in critic.py | ~30 lines | Low–Medium (mirrors extract.py pattern) | Resilience only |
| 4 | Reduce `critic.max_items_per_call` from 30 → 10 | 1 line | Low, but triples the number of LLM calls (cost ↑, may exceed Inngest step budget) | Per-call latency only |
| 5 | Slim critic prompt — send only cited pages, not full text | Multi-day | Medium (rewriting prompt assembly + tests; quality regression risk) | Root cause if prompt size is the driver |
| 6 | Make critic optional / soft-failable — partial results don't mark run as failed | Small | Low | Symptom (UI shows complete); does not fix underlying issue |
| 7 | Move critic out of the Inngest step into a fan-out (mirrors plan C for stage 7) | Multi-day | Medium | Inngest-level resilience; per-batch retry |

Combinations are possible — e.g. (3) + (4), or (1) + (3).

## What "fixed" looks like

- Critic completes for documents up to 50 pages without timing out > 5 % of the time.
- A timeout on one batch does not fail the whole stage (per-batch granularity).
- `parse_status: complete` is the default end state, with `partially_processed` reserved for genuine extraction gaps.
- No regression in critic quality (auto-reject precision/recall vs current baseline) — needs to be measured against ground truth.

## Suggested research order

1. Add structured logging around critic LLM calls (input token count, response time, model, batch index). Cheap; one PR. Run for a few real documents to gather data.
2. From the data, decide whether the bottleneck is Anthropic-side latency, prompt size, or transient capacity issues.
3. Pick from the option space above with evidence rather than guessing.

## References

- Failing doc: `c01494df-95b5-40de-9627-90a650dc7b9a` (HO3_sample.pdf, 22 pages, 147 obligations + 165 risks).
- Code: `backend/app/worker/tasks/critic.py`, `backend/app/services/llm.py:33-56`.
- Config: `backend/config.yaml` `critic:` block.
- Related: `docs/plan_stage7_fanout.md` (same Cloudflare-Inngest constraint applies upstream).
- Memory: `project_stage7_cloudflare_524.md` (root-cause pattern for stuck-at-extraction docs).
