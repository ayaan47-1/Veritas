# Fuzzy Quote Verification Fallback

## Problem

The verification stage (stage 9) uses exact string matching to locate LLM-extracted quotes in document text. When the LLM slightly truncates, garbles, or modifies a quote (e.g., "lien or lie" vs "lien or lien claim"), the match fails and the item is rejected with 0 confidence — even though the content genuinely exists in the document. This defeats the core purpose of the app.

## Design

### Two-pass verification

1. **Pass 1 — Exact match** (current behavior). `str.find()` on normalized text. If found, full confidence — no changes.
2. **Pass 2 — Fuzzy fallback**. If exact match fails, use `difflib.SequenceMatcher` to find the best-matching window in each page's normalized text. Accept if similarity ratio >= configurable threshold (default 0.85).

### Fuzzy matching algorithm

For each page, use a sliding window approach:
- Window size = length of the normalized quote +/- 20% tolerance
- Slide across `page.normalized_text`, compute `SequenceMatcher.ratio()` for each window
- Track the best match across all pages
- Accept if best ratio >= threshold

Optimization: before sliding, check if `SequenceMatcher.ratio()` on the full page text is above a minimum floor (0.3) to skip pages with no relevant content.

### When fuzzy matches

- Store the **actual page text** (the matched window) as the evidence quote, not the LLM's version
- Record `verification_method = "fuzzy"` and `fuzzy_similarity` on the evidence record
- The scoring stage applies a configurable penalty (default: -10) for fuzzy-verified items

### Config knobs (`config.yaml`)

```yaml
verification:
  fuzzy_threshold: 0.85      # minimum similarity ratio to accept
  fuzzy_penalty: -10          # scoring penalty for fuzzy-verified evidence
```

### Scoring integration

In `tasks/score.py`, check if any evidence for an item has `verification_method == "fuzzy"`. If so, apply `fuzzy_penalty` from config.

## Files to modify

- `backend/config.yaml` — add `verification` section
- `backend/app/worker/tasks/verify.py` — add `_fuzzy_find_quote_in_pages()`, update `_verify_obligations()` and `_verify_risks()` to call it as fallback
- `backend/app/worker/tasks/score.py` — add fuzzy penalty signal
- `backend/app/models/obligation_evidence.py` — add `verification_method` and `fuzzy_similarity` nullable columns
- `backend/app/models/risk_evidence.py` — same columns
- Alembic migration for the new columns

## What does NOT change

- Exact-match items are unaffected — no behavior change for currently-passing verification
- No LLM calls added — this is purely deterministic string comparison
- Evidence integrity preserved — we store what's actually in the document
