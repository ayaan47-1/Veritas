# CSV / Excel Export — Design

**Date:** 2026-04-23
**Branch:** `feature/csv-excel-export`
**Status:** Design approved, implementation plan pending

## Problem

VeritasLayer users need to get obligations and risks data OUT of the product into Yardi, MRI, and Excel. Without export, the product is a dead-end for operational reviewers who run their workflows in external systems.

## Goal

Add CSV and XLSX export for obligations and risks, respecting the currently-applied filter state from the list views.

## Non-goals

- Modifying the existing `GET /obligations` or `GET /risks` endpoints.
- Modifying the `Obligation` / `Risk` models or migrations.
- Adding new filter parameters (e.g., text search) to either the list or export endpoints.
- Refactoring `list_obligations` / `list_risks` to share filter code with the new export endpoints (out of scope and violates the "don't touch existing list endpoints" constraint).
- Server-side export history / audit trail. Exports are ephemeral downloads; no DB persistence.

## Architecture

Two new FastAPI endpoints in a new router `backend/app/routers/exports.py`, mounted in `backend/app/main.py`:

- `GET /exports/obligations?format=csv|xlsx&asset_id=&status=&severity=&document_id=`
- `GET /exports/risks?format=csv|xlsx&asset_id=&status=&severity=&risk_type=&document_id=`

`format` is optional and defaults to `csv`. Both endpoints accept the same filter subset that the corresponding list endpoint supports today.

### Access control

Reuse `require_asset_scope("asset_id", required_for_non_admin=True)`:

- Admins may omit `asset_id` → export across all assets.
- Non-admins MUST pass an `asset_id` they're assigned to, otherwise 403.

### Row cap

Hard cap of **50,000 rows** per export, configurable via `exports.max_rows` in `backend/config.yaml`. Enforced by `query.count()` before streaming. If exceeded:

```
HTTP 413 Payload Too Large
{"detail": "Export exceeds 50000 rows; tighten filters"}
```

## Components

### `backend/app/routers/exports.py`

Pure functions + two route handlers. No shared state.

1. **`_build_obligation_query(db, status, severity, asset_id, document_id) -> Query`** — returns a SQLAlchemy `Query` with all filters applied plus eager-load hints. Parallel to the list endpoint's filter block (not extracted — see Non-goals).
2. **`_build_risk_query(db, status, severity, risk_type, asset_id, document_id) -> Query`** — same shape for risks.
3. **`_resolve_rows(db, query, entity)` → `Iterator[RowContext]`** — batched iteration via `query.yield_per(500)`, joined to `Asset`, `Document`, primary evidence row, and latest review. Evidence joined via correlated subquery selecting `MIN(created_at), tiebreak MIN(id)`. Latest review joined via correlated subquery selecting `MAX(created_at)` → then `User.email`.
4. **`_serialize_obligation_row(ctx) -> list[str]`** — produces the 17-column row per the spec below.
5. **`_serialize_risk_row(ctx) -> list[str]`** — produces the 16-column row per the spec below.
6. **`_stream_csv(columns, row_iter) -> StreamingResponse`** — yields header row then data rows using the `csv` stdlib module writing to a per-request `io.StringIO` buffer, flushed per row.
7. **`_build_xlsx(columns, row_iter, sheet_name) -> Response`** — openpyxl workbook with:
   - Bold header row, `freeze_panes = "A2"`.
   - Severity column conditional fill: `critical` → red (`FFEF4444`), `high` → orange (`FFF97316`), `medium` → yellow (`FFEAB308`), `low` → blue (`FF3B82F6`). Colors match `SeverityBadge` component.
   - Column widths: ID columns 38, numeric columns 12, date columns 18, `text` / `evidence_quote` columns 60. Others 20.
   - Written to `BytesIO`, returned as a single `Response` (openpyxl cannot stream).
8. **`_filename(entity: str, asset: Asset | None, ext: str) -> str`** — `{entity}_{slug}_{YYYY-MM-DD}.{ext}` where `slug = "all"` if `asset is None` else lowercase alnum with spaces → `_` and other non-alnum stripped, collapsed `_+` → `_`.

### Router registration

Add one line to `backend/app/main.py` near the other router includes: `app.include_router(exports.router)`.

### `backend/requirements.txt`

Add `openpyxl>=3.1,<4.0`. (Confirmed not present today; spec assumption was incorrect.)

### `backend/config.yaml`

Add:

```yaml
exports:
  max_rows: 50000
```

## Column specification

### Obligations (17 columns, in order)

| # | Column | Source |
|---|---|---|
| 1 | `id` | `obligation.id` |
| 2 | `asset_name` | `asset.name` |
| 3 | `document_filename` | `document.source_name` |
| 4 | `obligation_type` | `obligation.obligation_type.value` |
| 5 | `text` | `obligation.obligation_text` |
| 6 | `severity` | `obligation.llm_severity.value if obligation.llm_severity else obligation.severity.value` |
| 7 | `system_confidence` | `obligation.system_confidence` |
| 8 | `llm_quality_confidence` | `obligation.llm_quality_confidence` or empty |
| 9 | `status` | `obligation.status.value` |
| 10 | `deadline` | `obligation.due_date.isoformat()` or empty |
| 11 | `evidence_quote` | primary evidence `.quote` or empty |
| 12 | `evidence_page_number` | primary evidence `.page_number` or empty |
| 13 | `evidence_char_start` | primary evidence `.raw_char_start` or empty |
| 14 | `evidence_char_end` | primary evidence `.raw_char_end` or empty |
| 15 | `created_at` | `obligation.created_at.isoformat()` (UTC) |
| 16 | `last_reviewed_at` | latest review `.created_at.isoformat()` or empty |
| 17 | `reviewer_email` | latest review's reviewer `user.email` or empty |

### Risks (16 columns, in order)

Same as obligations, minus `deadline`, with `obligation_type` replaced by `risk_type`, and field names sourced from `risk.risk_type`, `risk.risk_text`, etc.

### Primary evidence selection

`MIN(created_at) ASC, id ASC` — lowest created_at wins; ID tie-break ensures determinism.

### Latest review selection

`MAX(created_at) DESC` — newest review wins, regardless of decision. The spec calls for "last_reviewed_at" which is agnostic to approve/reject, so we use the most recent review record.

### Char offsets

Export uses `raw_char_start` / `raw_char_end`. Raw offsets map to the original PDF text and are what downstream tools (Yardi field imports, BI dashboards) will expect.

## Filename

Format: `{entity}_{asset_slug_or_all}_{YYYY-MM-DD}.{csv|xlsx}`.

Examples:

- `obligations_willow_creek_tower_2026-04-23.csv`
- `risks_all_2026-04-23.xlsx` (admin, no asset filter)

Slug rules:

- Lowercase the asset name.
- Replace spaces with `_`.
- Strip any character that isn't `a-z`, `0-9`, `_`, `-`.
- Collapse runs of `_` into single `_`.

Filename is returned in `Content-Disposition: attachment; filename="..."`. Backend owns the filename; the pre-existing `csvFilename` helper in `frontend/src/lib/csv.ts` is NOT used for the new download flow.

## Data flow

1. Request → Clerk JWT auth → `require_asset_scope` check.
2. Build filtered query via `_build_*_query`.
3. `query.count()` — if > `exports.max_rows` → 413.
4. Fetch asset row (for filename) — `None` if no asset filter.
5. Dispatch on `format`:
   - `csv`: `StreamingResponse(generator, media_type="text/csv; charset=utf-8", headers=Content-Disposition)`. Generator yields header line then per-row lines from `_resolve_rows` with `yield_per(500)`.
   - `xlsx`: build workbook in memory, return as `Response(content=bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=Content-Disposition)`.

Memory envelope for CSV: constant (stream). For XLSX: ~O(N × 500B) bytes resident; at the 50k cap that's ~25 MB, acceptable.

## Frontend

### `frontend/src/lib/csv.ts`

Add (do NOT modify existing `downloadCsv` / `csvFilename`):

```typescript
export async function downloadExport(
  endpoint: "obligations" | "risks",
  params: URLSearchParams,
  format: "csv" | "xlsx",
  token: string,
): Promise<void>
```

Behavior:

- `GET ${NEXT_PUBLIC_API_URL}/exports/${endpoint}?format=${format}&${params}` with `Authorization: Bearer ${token}`.
- On non-2xx: parse JSON body, throw `Error(detail)`.
- On 2xx: read `Content-Disposition` header, extract filename (simple regex `filename="([^"]+)"` with fallback to `${endpoint}.${format}`), blob-download via temporary anchor element.

### `ObligationsClientPage.tsx` and `RisksClientPage.tsx`

Add two buttons in the filter/toolbar row:

- "Download CSV"
- "Download Excel"

State additions:

```typescript
const [downloadingFormat, setDownloadingFormat] = useState<"csv" | "xlsx" | null>(null);
const [downloadError, setDownloadError] = useState<string | null>(null);
```

On click:

1. `setDownloadingFormat(format)`.
2. Build `URLSearchParams` from the same filter state used for the list fetch.
3. `await downloadExport(...)`.
4. On error: `setDownloadError(error.message)`.
5. Finally: `setDownloadingFormat(null)`.

Both buttons disabled while `downloadingFormat !== null`; the active one shows a small spinner. Error surfaces as a red text line near the buttons and auto-clears after 6 seconds or on next click.

## Error handling

| Condition | Status | Response |
|---|---|---|
| Missing/invalid Clerk token | 401 | existing auth dep |
| Non-admin, no `asset_id` | 403 | existing asset scope dep |
| Non-admin, unauthorized `asset_id` | 403 | existing asset scope dep |
| Result set > `exports.max_rows` | 413 | `{"detail": "Export exceeds N rows; tighten filters"}` |
| Unknown `format` | 422 | FastAPI validation error |
| Empty result set | 200 | valid CSV with header row only, or empty-body XLSX with header row only |

Streaming CSV errors mid-response → connection drops; browser shows broken download. Acceptable trade-off for flat-memory streaming. Errors logged via existing logger.

## Testing

`backend/tests/test_exports_router.py` — 7 tests:

1. **CSV obligations basic** — seed 2 obligations with evidence, fetch `/exports/obligations?asset_id=...`, assert response is `text/csv`, header line matches the 17-column spec exactly, 2 data rows with the correct severity override (`llm_severity` wins).
2. **CSV obligations with filters** — seed 3 obligations of mixed severity/status, fetch with `status=confirmed&severity=critical`, assert only the matching row exports.
3. **XLSX obligations cell colors** — seed obligations of each severity, fetch `?format=xlsx`, load response bytes with `openpyxl.load_workbook(BytesIO(...))`, assert: header row font.bold True, `freeze_panes == "A2"`, severity cell fill matches the hex codes for each severity tier.
4. **CSV risks basic** — mirror test #1 for risks, 16 columns, asserts `risk_type` column populated.
5. **Permission denied** — non-admin user assigned only to asset A requests export for asset B → 403.
6. **Empty result returns valid empty CSV** — filter matching zero rows → 200, header line only, no data rows.
7. **Row cap triggers 413** — stub `exports.max_rows = 2`, seed 3 obligations → 413 with the "tighten filters" detail.

Test style: follows the pattern in `test_assets_router.py` / `test_router_behaviors.py`. Exact fixture strategy (in-memory SQLite vs mocked session) confirmed during plan-writing after reading one of those files.

## Deliverables

1. `backend/app/routers/exports.py` — new router
2. `backend/app/main.py` — register router
3. `backend/requirements.txt` — add `openpyxl>=3.1,<4.0`
4. `backend/config.yaml` — add `exports.max_rows: 50000`
5. `frontend/src/lib/csv.ts` — add `downloadExport` helper
6. `frontend/src/app/obligations/ObligationsClientPage.tsx` — add CSV/Excel buttons wired to current filter state
7. `frontend/src/app/risks/RisksClientPage.tsx` — same for risks
8. `backend/tests/test_exports_router.py` — 7 tests
9. `CLAUDE.md` — add `/exports/obligations` and `/exports/risks` to the API list section

## Verification

After implementation:

```bash
python3 -m compileall backend/app -q
python3 -m pytest -q backend/tests
cd frontend && npm run lint && npm run build
```

All must pass. No schema migrations; no new environment variables.
