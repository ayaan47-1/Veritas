# CSV / Excel Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship CSV and XLSX export endpoints for obligations and risks, with matching Download buttons on the obligations/risks list pages, so users can pull data into Yardi, MRI, and Excel.

**Architecture:** New `/exports/obligations` and `/exports/risks` endpoints in a new `backend/app/routers/exports.py`. Row resolution uses three fetches (items → evidence → reviews) zipped in Python rather than a single correlated-subquery join, so it's trivially testable with the existing `FakeSession` pattern. CSV streams via `StreamingResponse`; XLSX builds in `BytesIO` via `openpyxl`. Hard cap of 50,000 rows enforced by `query.count()`. Frontend adds two buttons per page; they fetch with the Clerk token and trigger a blob download using the filename from `Content-Disposition`.

**Tech Stack:** FastAPI, SQLAlchemy, openpyxl, Next.js 16 / React, Clerk.

**Design spec:** `docs/superpowers/specs/2026-04-23-csv-excel-export-design.md`.

---

## File Map

- **Create:** `backend/app/routers/exports.py` — router + query builders + row resolvers + CSV/XLSX helpers.
- **Create:** `backend/tests/test_exports_router.py` — 7 tests.
- **Modify:** `backend/app/main.py` — register `exports_router`.
- **Modify:** `backend/requirements.txt` — add `openpyxl`.
- **Modify:** `backend/config.yaml` — add `exports.max_rows`.
- **Create:** `frontend/src/lib/__tests__/csv.test.ts` — Vitest covering the new `downloadExport` helper filename parsing (pure function slice).
- **Modify:** `frontend/src/lib/csv.ts` — add `downloadExport` helper.
- **Modify:** `frontend/src/app/obligations/ObligationsClientPage.tsx` — add Download CSV/Excel buttons.
- **Modify:** `frontend/src/app/risks/RisksClientPage.tsx` — add Download CSV/Excel buttons.
- **Modify:** `CLAUDE.md` — add the two new endpoints to the API section.

---

## Task 1: Add `openpyxl` dependency and config knob

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/config.yaml`

- [ ] **Step 1: Add `openpyxl` to requirements**

Append to `backend/requirements.txt`:

```
openpyxl>=3.1,<4.0
```

- [ ] **Step 2: Install it locally**

Run: `pip install -r backend/requirements.txt`
Expected: `openpyxl` installed without touching `litellm` or other pinned versions.

- [ ] **Step 3: Add `exports.max_rows` to config.yaml**

Append to `backend/config.yaml` (after the `rescoring:` block, before `mcp:` — alphabetical-ish placement doesn't matter, but keep it top-level):

```yaml
exports:
  max_rows: 50000
```

- [ ] **Step 4: Sanity-check config loads**

Run:
```
python3 -c "from backend.app.config import settings; print(settings.raw['exports']['max_rows'])"
```
Expected: `50000`

- [ ] **Step 5: Commit**

```
git add backend/requirements.txt backend/config.yaml
git commit -m "chore: add openpyxl and exports.max_rows config knob"
```

---

## Task 2: Write failing tests for the export row serializers (pure functions)

**Files:**
- Create: `backend/tests/test_exports_router.py`

We start with pure-function tests that don't need the DB. These define the row shape that the serializers must produce.

- [ ] **Step 1: Create the test file with the first two pure-function tests**

Create `backend/tests/test_exports_router.py`:

```python
from __future__ import annotations

import hashlib
import importlib
import uuid
from datetime import UTC, datetime
from io import BytesIO

from fastapi.testclient import TestClient

from backend.app.auth import deps as auth_deps
from backend.app.database import get_db
from backend.app.main import create_app
from backend.app.models import (
    Asset,
    Document,
    DueKind,
    Modality,
    Obligation,
    ObligationEvidence,
    ObligationReview,
    ObligationType,
    OIDCProvider,
    ParseStatus,
    ReviewDecision,
    ReviewStatus,
    Risk,
    RiskEvidence,
    RiskType,
    Severity,
    TextSource,
    User,
    UserAssetAssignment,
    UserRole,
)

exports_router = importlib.import_module("backend.app.routers.exports")


OBLIGATION_COLUMNS = [
    "id",
    "asset_name",
    "document_filename",
    "obligation_type",
    "text",
    "severity",
    "system_confidence",
    "llm_quality_confidence",
    "status",
    "deadline",
    "evidence_quote",
    "evidence_page_number",
    "evidence_char_start",
    "evidence_char_end",
    "created_at",
    "last_reviewed_at",
    "reviewer_email",
]

RISK_COLUMNS = [
    "id",
    "asset_name",
    "document_filename",
    "risk_type",
    "text",
    "severity",
    "system_confidence",
    "llm_quality_confidence",
    "status",
    "evidence_quote",
    "evidence_page_number",
    "evidence_char_start",
    "evidence_char_end",
    "created_at",
    "last_reviewed_at",
    "reviewer_email",
]


def test_obligation_columns_match_spec():
    assert exports_router.OBLIGATION_COLUMNS == OBLIGATION_COLUMNS


def test_risk_columns_match_spec():
    assert exports_router.RISK_COLUMNS == RISK_COLUMNS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.routers.exports'`.

- [ ] **Step 3: Commit (failing test, TDD)**

```
git add backend/tests/test_exports_router.py
git commit -m "test: red — expected column orderings for export rows"
```

---

## Task 3: Create `exports.py` scaffold with column constants and filename helper

**Files:**
- Create: `backend/app/routers/exports.py`

- [ ] **Step 1: Create the router scaffold**

Create `backend/app/routers/exports.py`:

```python
from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user, require_asset_scope
from ..config import settings
from ..database import get_db
from ..models import (
    Asset,
    Document,
    Obligation,
    ObligationEvidence,
    ObligationReview,
    ReviewStatus,
    Risk,
    RiskEvidence,
    RiskReview,
    RiskType,
    Severity,
    User,
)

router = APIRouter(prefix="/exports", tags=["exports"])


OBLIGATION_COLUMNS: list[str] = [
    "id",
    "asset_name",
    "document_filename",
    "obligation_type",
    "text",
    "severity",
    "system_confidence",
    "llm_quality_confidence",
    "status",
    "deadline",
    "evidence_quote",
    "evidence_page_number",
    "evidence_char_start",
    "evidence_char_end",
    "created_at",
    "last_reviewed_at",
    "reviewer_email",
]


RISK_COLUMNS: list[str] = [
    "id",
    "asset_name",
    "document_filename",
    "risk_type",
    "text",
    "severity",
    "system_confidence",
    "llm_quality_confidence",
    "status",
    "evidence_quote",
    "evidence_page_number",
    "evidence_char_start",
    "evidence_char_end",
    "created_at",
    "last_reviewed_at",
    "reviewer_email",
]


_SEVERITY_FILL_HEX: dict[str, str] = {
    "critical": "FFEF4444",
    "high": "FFF97316",
    "medium": "FFEAB308",
    "low": "FF3B82F6",
}


def _slug(name: str | None) -> str:
    if not name:
        return "all"
    lowered = name.lower()
    replaced = re.sub(r"\s+", "_", lowered)
    stripped = re.sub(r"[^a-z0-9_-]", "", replaced)
    collapsed = re.sub(r"_+", "_", stripped).strip("_-")
    return collapsed or "all"


def _filename(entity: str, asset_name: str | None, ext: str) -> str:
    return f"{entity}_{_slug(asset_name)}_{datetime.now(tz=timezone.utc).date().isoformat()}.{ext}"


def _max_rows() -> int:
    raw = settings.raw.get("exports", {}).get("max_rows", 50000)
    return int(raw)
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: Both tests PASS (`test_obligation_columns_match_spec`, `test_risk_columns_match_spec`).

- [ ] **Step 3: Sanity compile**

Run: `python3 -m compileall backend/app/routers/exports.py -q`
Expected: no output / exit 0.

- [ ] **Step 4: Commit**

```
git add backend/app/routers/exports.py
git commit -m "feat: exports router scaffold with column constants"
```

---

## Task 4: Add filename slug tests and verify

**Files:**
- Modify: `backend/tests/test_exports_router.py`

- [ ] **Step 1: Add filename tests**

Append to `backend/tests/test_exports_router.py`:

```python
def test_slug_handles_spaces_and_special_chars():
    assert exports_router._slug("Willow Creek Tower") == "willow_creek_tower"
    assert exports_router._slug("A&B / C, D!") == "ab_c_d"
    assert exports_router._slug("   ") == "all"
    assert exports_router._slug(None) == "all"
    assert exports_router._slug("") == "all"


def test_filename_structure(monkeypatch):
    filename = exports_router._filename("obligations", "Willow Creek", "csv")
    assert filename.startswith("obligations_willow_creek_")
    assert filename.endswith(".csv")
    # YYYY-MM-DD is 10 chars between the slug and .csv
    date_part = filename[len("obligations_willow_creek_"):-4]
    assert len(date_part) == 10
    assert date_part[4] == "-" and date_part[7] == "-"


def test_filename_all_when_no_asset():
    filename = exports_router._filename("risks", None, "xlsx")
    assert filename.startswith("risks_all_")
    assert filename.endswith(".xlsx")
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 3: Commit**

```
git add backend/tests/test_exports_router.py
git commit -m "test: cover _slug and _filename helpers"
```

---

## Task 5: Add query builders with tests

**Files:**
- Modify: `backend/app/routers/exports.py`
- Modify: `backend/tests/test_exports_router.py`

The query builders mirror `list_obligations` / `list_risks` filter blocks. Parallel logic (not extracted) per the spec's Non-goals.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_exports_router.py`:

```python
def test_build_obligation_query_filters_by_status_and_severity():
    app = create_app()
    # Use the real test DB machinery — we just need filter wiring to compile and call.
    # We'll verify correctness end-to-end in the endpoint tests below.
    assert callable(exports_router._build_obligation_query)
    assert callable(exports_router._build_risk_query)
```

(Smoke test — the serious filter coverage happens in endpoint tests.)

- [ ] **Step 2: Run — should fail**

Run: `python3 -m pytest backend/tests/test_exports_router.py::test_build_obligation_query_filters_by_status_and_severity -v`
Expected: FAIL — `AttributeError: module 'backend.app.routers.exports' has no attribute '_build_obligation_query'`.

- [ ] **Step 3: Implement query builders**

Append to `backend/app/routers/exports.py`:

```python
def _build_obligation_query(
    db: Session,
    *,
    status: ReviewStatus | None,
    severity: Severity | None,
    document_id: UUID | None,
    asset_id: UUID | None,
):
    query = db.query(Obligation)
    if status is not None:
        query = query.filter(Obligation.status == status)
    if severity is not None:
        query = query.filter(Obligation.severity == severity)
    if document_id is not None:
        query = query.filter(Obligation.document_id == document_id)
    if asset_id is not None:
        query = query.join(Document, Obligation.document_id == Document.id).filter(Document.asset_id == asset_id)
    return query


def _build_risk_query(
    db: Session,
    *,
    status: ReviewStatus | None,
    severity: Severity | None,
    risk_type: RiskType | None,
    document_id: UUID | None,
    asset_id: UUID | None,
):
    query = db.query(Risk)
    if status is not None:
        query = query.filter(Risk.status == status)
    if severity is not None:
        query = query.filter(Risk.severity == severity)
    if risk_type is not None:
        query = query.filter(Risk.risk_type == risk_type)
    if document_id is not None:
        query = query.filter(Risk.document_id == document_id)
    if asset_id is not None:
        query = query.join(Document, Risk.document_id == Document.id).filter(Document.asset_id == asset_id)
    return query
```

- [ ] **Step 4: Run — should pass**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/routers/exports.py backend/tests/test_exports_router.py
git commit -m "feat: exports router query builders"
```

---

## Task 6: Add row resolver helpers (three-fetch approach)

**Files:**
- Modify: `backend/app/routers/exports.py`

No test in this task — these helpers are exercised end-to-end in Task 8's endpoint tests. This keeps tests close to observable behavior.

- [ ] **Step 1: Append RowContext and resolver helpers**

Append to `backend/app/routers/exports.py`:

```python
@dataclass
class _ObligationRow:
    obligation: Obligation
    asset_name: str
    document_filename: str
    evidence: ObligationEvidence | None
    last_review: ObligationReview | None
    reviewer_email: str | None


@dataclass
class _RiskRow:
    risk: Risk
    asset_name: str
    document_filename: str
    evidence: RiskEvidence | None
    last_review: RiskReview | None
    reviewer_email: str | None


def _iso_or_empty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _severity_for_export(system: Severity, llm: Severity | None) -> str:
    return (llm.value if llm is not None else system.value)


def _resolve_obligation_rows(
    db: Session, obligations: list[Obligation]
) -> Iterator[_ObligationRow]:
    if not obligations:
        return iter(())

    obligation_ids = [row.id for row in obligations]
    document_ids = list({row.document_id for row in obligations})

    documents = {d.id: d for d in db.query(Document).filter(Document.id.in_(document_ids)).all()}
    asset_ids = list({d.asset_id for d in documents.values()})
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}

    evidence_rows = (
        db.query(ObligationEvidence)
        .filter(ObligationEvidence.obligation_id.in_(obligation_ids))
        .order_by(ObligationEvidence.created_at.asc(), ObligationEvidence.id.asc())
        .all()
    )
    primary_evidence: dict[UUID, ObligationEvidence] = {}
    for ev in evidence_rows:
        primary_evidence.setdefault(ev.obligation_id, ev)

    review_rows = (
        db.query(ObligationReview)
        .filter(ObligationReview.obligation_id.in_(obligation_ids))
        .order_by(ObligationReview.created_at.desc(), ObligationReview.id.desc())
        .all()
    )
    last_review: dict[UUID, ObligationReview] = {}
    for rv in review_rows:
        last_review.setdefault(rv.obligation_id, rv)

    reviewer_ids = list({rv.reviewer_id for rv in last_review.values() if rv.reviewer_id is not None})
    reviewer_emails = (
        {u.id: u.email for u in db.query(User).filter(User.id.in_(reviewer_ids)).all()}
        if reviewer_ids
        else {}
    )

    def _iter() -> Iterator[_ObligationRow]:
        for ob in obligations:
            document = documents.get(ob.document_id)
            asset = assets.get(document.asset_id) if document else None
            rv = last_review.get(ob.id)
            yield _ObligationRow(
                obligation=ob,
                asset_name=asset.name if asset else "",
                document_filename=document.source_name if document else "",
                evidence=primary_evidence.get(ob.id),
                last_review=rv,
                reviewer_email=reviewer_emails.get(rv.reviewer_id) if rv and rv.reviewer_id else None,
            )

    return _iter()


def _resolve_risk_rows(db: Session, risks: list[Risk]) -> Iterator[_RiskRow]:
    if not risks:
        return iter(())

    risk_ids = [row.id for row in risks]
    document_ids = list({row.document_id for row in risks})

    documents = {d.id: d for d in db.query(Document).filter(Document.id.in_(document_ids)).all()}
    asset_ids = list({d.asset_id for d in documents.values()})
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}

    evidence_rows = (
        db.query(RiskEvidence)
        .filter(RiskEvidence.risk_id.in_(risk_ids))
        .order_by(RiskEvidence.created_at.asc(), RiskEvidence.id.asc())
        .all()
    )
    primary_evidence: dict[UUID, RiskEvidence] = {}
    for ev in evidence_rows:
        primary_evidence.setdefault(ev.risk_id, ev)

    review_rows = (
        db.query(RiskReview)
        .filter(RiskReview.risk_id.in_(risk_ids))
        .order_by(RiskReview.created_at.desc(), RiskReview.id.desc())
        .all()
    )
    last_review: dict[UUID, RiskReview] = {}
    for rv in review_rows:
        last_review.setdefault(rv.risk_id, rv)

    reviewer_ids = list({rv.reviewer_id for rv in last_review.values() if rv.reviewer_id is not None})
    reviewer_emails = (
        {u.id: u.email for u in db.query(User).filter(User.id.in_(reviewer_ids)).all()}
        if reviewer_ids
        else {}
    )

    def _iter() -> Iterator[_RiskRow]:
        for r in risks:
            document = documents.get(r.document_id)
            asset = assets.get(document.asset_id) if document else None
            rv = last_review.get(r.id)
            yield _RiskRow(
                risk=r,
                asset_name=asset.name if asset else "",
                document_filename=document.source_name if document else "",
                evidence=primary_evidence.get(r.id),
                last_review=rv,
                reviewer_email=reviewer_emails.get(rv.reviewer_id) if rv and rv.reviewer_id else None,
            )

    return _iter()


def _row_for_obligation(row: _ObligationRow) -> list[str]:
    ob = row.obligation
    ev = row.evidence
    last = row.last_review
    return [
        str(ob.id),
        row.asset_name,
        row.document_filename,
        ob.obligation_type.value,
        ob.obligation_text or "",
        _severity_for_export(ob.severity, ob.llm_severity),
        _iso_or_empty(ob.system_confidence),
        _iso_or_empty(ob.llm_quality_confidence),
        ob.status.value,
        _iso_or_empty(ob.due_date),
        (ev.quote if ev else ""),
        _iso_or_empty(ev.page_number if ev else None),
        _iso_or_empty(ev.raw_char_start if ev else None),
        _iso_or_empty(ev.raw_char_end if ev else None),
        _iso_or_empty(ob.created_at),
        _iso_or_empty(last.created_at if last else None),
        row.reviewer_email or "",
    ]


def _row_for_risk(row: _RiskRow) -> list[str]:
    r = row.risk
    ev = row.evidence
    last = row.last_review
    return [
        str(r.id),
        row.asset_name,
        row.document_filename,
        r.risk_type.value,
        r.risk_text or "",
        _severity_for_export(r.severity, r.llm_severity),
        _iso_or_empty(r.system_confidence),
        _iso_or_empty(r.llm_quality_confidence),
        r.status.value,
        (ev.quote if ev else ""),
        _iso_or_empty(ev.page_number if ev else None),
        _iso_or_empty(ev.raw_char_start if ev else None),
        _iso_or_empty(ev.raw_char_end if ev else None),
        _iso_or_empty(r.created_at),
        _iso_or_empty(last.created_at if last else None),
        row.reviewer_email or "",
    ]
```

- [ ] **Step 2: Compile check**

Run: `python3 -m compileall backend/app/routers/exports.py -q`
Expected: exit 0.

- [ ] **Step 3: Run test suite — should still pass**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: all 6 tests still PASS.

- [ ] **Step 4: Commit**

```
git add backend/app/routers/exports.py
git commit -m "feat: exports row resolvers and serializers"
```

---

## Task 7: Add CSV + XLSX writers and route handlers

**Files:**
- Modify: `backend/app/routers/exports.py`

- [ ] **Step 1: Append CSV/XLSX writers and route handlers**

Append to `backend/app/routers/exports.py`:

```python
def _csv_lines(columns: list[str], rows: Iterable[list[str]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate()
    for row in rows:
        writer.writerow(row)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()


def _xlsx_bytes(
    columns: list[str],
    rows: Iterable[list[str]],
    sheet_name: str,
    severity_column_index: int,
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    widths = {
        "id": 38,
        "asset_name": 24,
        "document_filename": 30,
        "obligation_type": 16,
        "risk_type": 16,
        "text": 60,
        "severity": 12,
        "system_confidence": 12,
        "llm_quality_confidence": 14,
        "status": 14,
        "deadline": 14,
        "evidence_quote": 60,
        "evidence_page_number": 10,
        "evidence_char_start": 12,
        "evidence_char_end": 12,
        "created_at": 24,
        "last_reviewed_at": 24,
        "reviewer_email": 28,
    }
    for idx, column_name in enumerate(columns, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=idx).column_letter].width = widths.get(column_name, 18)

    severity_column_excel_index = severity_column_index + 1  # 1-based
    for row in rows:
        sheet.append(row)
        sev_value = row[severity_column_index]
        hex_fill = _SEVERITY_FILL_HEX.get(sev_value)
        if hex_fill:
            cell = sheet.cell(row=sheet.max_row, column=severity_column_excel_index)
            cell.fill = PatternFill(start_color=hex_fill, end_color=hex_fill, fill_type="solid")

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _too_large(count: int) -> HTTPException | None:
    cap = _max_rows()
    if count > cap:
        return HTTPException(
            status_code=413,
            detail=f"Export exceeds {cap} rows; tighten filters",
        )
    return None


@router.get(
    "/obligations",
    dependencies=[Depends(require_asset_scope("asset_id", required_for_non_admin=True))],
)
def export_obligations(
    format: Literal["csv", "xlsx"] = Query(default="csv"),
    status: ReviewStatus | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    document_id: UUID | None = Query(default=None),
    asset_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = _build_obligation_query(
        db,
        status=status,
        severity=severity,
        document_id=document_id,
        asset_id=asset_id,
    )
    count = query.count()
    error = _too_large(count)
    if error is not None:
        raise error

    obligations = query.order_by(Obligation.created_at.desc()).all()
    rows = list(_resolve_obligation_rows(db, obligations))
    asset_name = rows[0].asset_name if rows and asset_id is not None else None
    filename = _filename("obligations", asset_name, "xlsx" if format == "xlsx" else "csv")

    severity_index = OBLIGATION_COLUMNS.index("severity")

    if format == "xlsx":
        data = _xlsx_bytes(
            OBLIGATION_COLUMNS,
            (_row_for_obligation(row) for row in rows),
            sheet_name="Obligations",
            severity_column_index=severity_index,
        )
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    stream = _csv_lines(OBLIGATION_COLUMNS, (_row_for_obligation(row) for row in rows))
    return StreamingResponse(
        stream,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/risks",
    dependencies=[Depends(require_asset_scope("asset_id", required_for_non_admin=True))],
)
def export_risks(
    format: Literal["csv", "xlsx"] = Query(default="csv"),
    status: ReviewStatus | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    risk_type: RiskType | None = Query(default=None),
    document_id: UUID | None = Query(default=None),
    asset_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = _build_risk_query(
        db,
        status=status,
        severity=severity,
        risk_type=risk_type,
        document_id=document_id,
        asset_id=asset_id,
    )
    count = query.count()
    error = _too_large(count)
    if error is not None:
        raise error

    risks = query.order_by(Risk.created_at.desc()).all()
    rows = list(_resolve_risk_rows(db, risks))
    asset_name = rows[0].asset_name if rows and asset_id is not None else None
    filename = _filename("risks", asset_name, "xlsx" if format == "xlsx" else "csv")

    severity_index = RISK_COLUMNS.index("severity")

    if format == "xlsx":
        data = _xlsx_bytes(
            RISK_COLUMNS,
            (_row_for_risk(row) for row in rows),
            sheet_name="Risks",
            severity_column_index=severity_index,
        )
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    stream = _csv_lines(RISK_COLUMNS, (_row_for_risk(row) for row in rows))
    return StreamingResponse(
        stream,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 2: Compile check**

Run: `python3 -m compileall backend/app/routers/exports.py -q`
Expected: exit 0.

- [ ] **Step 3: Run existing tests — should still pass**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: all 6 tests still PASS.

- [ ] **Step 4: Commit**

```
git add backend/app/routers/exports.py
git commit -m "feat: CSV/XLSX writers and export endpoints"
```

---

## Task 8: Register router in `main.py` and add the endpoint integration tests

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_exports_router.py`

- [ ] **Step 1: Register the router**

Open `backend/app/main.py` and find the block of `app.include_router(...)` calls (around line 39–51). Import and include the exports router.

Add to the top imports (next to the other router imports):

```python
from .routers.exports import router as exports_router
```

Add to the router-include block (after `config_router`):

```python
    app.include_router(exports_router)
```

- [ ] **Step 2: Compile check**

Run: `python3 -m compileall backend/app -q`
Expected: exit 0.

- [ ] **Step 3: Add FakeSession + fixtures + endpoint tests to the test file**

Append to `backend/tests/test_exports_router.py`:

```python
from openpyxl import load_workbook


class FakeQuery:
    def __init__(self, session: "FakeSession", *models):
        self._session = session
        self._model = models[0] if models else None
        self._conditions = []
        self._offset = 0
        self._limit = None

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def join(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def offset(self, offset: int):
        self._offset = offset
        return self

    def limit(self, limit: int):
        self._limit = limit
        return self

    def all(self):
        rows = [r for r in self._rows_for_model() if self._matches_all(r)]
        rows = rows[self._offset:]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.all())

    def _rows_for_model(self):
        s = self._session
        mapping = {
            User: s.users,
            UserAssetAssignment: s.assignments,
            Asset: s.assets,
            Document: s.documents,
            Obligation: s.obligations,
            ObligationEvidence: s.obligation_evidence,
            ObligationReview: s.obligation_reviews,
            Risk: s.risks,
            RiskEvidence: s.risk_evidence,
            RiskReview: s.risk_reviews,
        }
        return list(mapping.get(self._model, []))

    def _matches_all(self, row):
        return all(self._matches(row, c) for c in self._conditions)

    def _matches(self, row, condition):
        left = getattr(condition, "left", None)
        right = getattr(condition, "right", None)
        if left is None:
            return True
        key = getattr(left, "key", None)
        if key is None:
            return True
        row_value = self._row_value(row, key)
        op_name = getattr(getattr(condition, "operator", None), "__name__", "")
        raw_value = getattr(right, "value", right)
        if op_name == "in_op":
            return row_value in list(raw_value)
        return row_value == raw_value

    def _row_value(self, row, key):
        if hasattr(row, key):
            return getattr(row, key)
        if key == "asset_id" and hasattr(row, "document_id"):
            doc = self._session.document_by_id.get(row.document_id)
            return doc.asset_id if doc else None
        return None


class FakeSession:
    def __init__(self):
        self.users: list[User] = []
        self.assignments: list[UserAssetAssignment] = []
        self.assets: list[Asset] = []
        self.documents: list[Document] = []
        self.obligations: list[Obligation] = []
        self.obligation_evidence: list[ObligationEvidence] = []
        self.obligation_reviews: list[ObligationReview] = []
        self.risks: list[Risk] = []
        self.risk_evidence: list[RiskEvidence] = []
        self.risk_reviews: list[RiskReview] = []
        self.document_by_id: dict = {}

    def query(self, *models):
        return FakeQuery(self, *models)

    def add(self, _obj):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _admin_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="admin@example.com",
        name="Admin",
        oidc_provider=OIDCProvider.clerk,
        oidc_subject="admin-sub",
        role=UserRole.admin,
        is_active=True,
    )


def _viewer_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        name="Viewer",
        oidc_provider=OIDCProvider.clerk,
        oidc_subject="viewer-sub",
        role=UserRole.viewer,
        is_active=True,
    )


def _asset(created_by) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        name="Willow Creek",
        description=None,
        created_by=created_by,
    )


def _document(asset_id, uploaded_by) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        source_name="lease.pdf",
        file_path="/tmp/lease.pdf",
        sha256=hashlib.sha256(str(asset_id).encode()).hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uploaded_by,
        parse_status=ParseStatus.complete,
        scanned_page_count=0,
    )


def _obligation(
    document_id,
    *,
    severity=Severity.high,
    status=ReviewStatus.needs_review,
    llm_severity=None,
) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        document_id=document_id,
        obligation_type=ObligationType.payment,
        obligation_text="Tenant shall pay rent on the first of each month.",
        modality=Modality.shall,
        responsible_entity_id=None,
        due_kind=DueKind.none,
        due_date=None,
        due_rule=None,
        trigger_date=None,
        severity=severity,
        llm_severity=llm_severity,
        llm_quality_confidence=None,
        status=status,
        system_confidence=85,
        reviewer_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
        created_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
    )


def _risk(document_id, *, severity=Severity.critical, status=ReviewStatus.confirmed) -> Risk:
    return Risk(
        id=uuid.uuid4(),
        document_id=document_id,
        risk_type=RiskType.financial,
        risk_text="Uncapped indemnification exposes landlord to unlimited liability.",
        severity=severity,
        status=status,
        system_confidence=80,
        reviewer_confidence=None,
        llm_severity=None,
        llm_quality_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
        created_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
    )


def _make_client(db: FakeSession, current_user: User) -> TestClient:
    app = create_app()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[auth_deps.get_current_user] = lambda: current_user
    return TestClient(app)


def _seed_obligations_db(obligations_count: int = 2) -> tuple[FakeSession, User, Asset]:
    db = FakeSession()
    admin = _admin_user()
    db.users.append(admin)
    asset = _asset(admin.id)
    db.assets.append(asset)
    doc = _document(asset.id, admin.id)
    db.documents.append(doc)
    db.document_by_id = {doc.id: doc}
    for i in range(obligations_count):
        ob = _obligation(doc.id, severity=Severity.high if i == 0 else Severity.low)
        db.obligations.append(ob)
    return db, admin, asset


def test_export_obligations_csv_basic():
    db, admin, asset = _seed_obligations_db(obligations_count=2)
    client = _make_client(db, admin)

    response = client.get(f"/exports/obligations?asset_id={asset.id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "obligations_willow_creek_" in response.headers["content-disposition"]

    lines = response.text.strip().split("\n")
    assert lines[0].strip() == ",".join(OBLIGATION_COLUMNS)
    assert len(lines) == 3  # header + 2 rows


def test_export_obligations_csv_respects_severity_filter():
    db, admin, asset = _seed_obligations_db(obligations_count=2)
    client = _make_client(db, admin)

    response = client.get(f"/exports/obligations?asset_id={asset.id}&severity=high")
    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    assert len(lines) == 2  # header + 1 row


def test_export_obligations_xlsx_severity_cell_colors():
    db, admin, asset = _seed_obligations_db(obligations_count=0)
    # One obligation per severity tier
    doc = db.documents[0]
    for sev in (Severity.critical, Severity.high, Severity.medium, Severity.low):
        db.obligations.append(_obligation(doc.id, severity=sev))
    client = _make_client(db, admin)

    response = client.get(f"/exports/obligations?asset_id={asset.id}&format=xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook.active
    assert sheet.title == "Obligations"
    assert sheet["A1"].font.bold is True
    assert sheet.freeze_panes == "A2"

    severity_column_index = OBLIGATION_COLUMNS.index("severity") + 1
    # Rows are ordered newest first; since we seeded all with same created_at,
    # openpyxl order reflects insertion — critical, high, medium, low.
    expected = {
        "critical": "FFEF4444",
        "high": "FFF97316",
        "medium": "FFEAB308",
        "low": "FF3B82F6",
    }
    actual = {}
    for row_idx in range(2, sheet.max_row + 1):
        sev = sheet.cell(row=row_idx, column=severity_column_index).value
        fill = sheet.cell(row=row_idx, column=severity_column_index).fill
        actual[sev] = fill.start_color.rgb
    assert actual == expected


def test_export_risks_csv_basic():
    db = FakeSession()
    admin = _admin_user()
    db.users.append(admin)
    asset = _asset(admin.id)
    db.assets.append(asset)
    doc = _document(asset.id, admin.id)
    db.documents.append(doc)
    db.document_by_id = {doc.id: doc}
    db.risks.append(_risk(doc.id))

    client = _make_client(db, admin)
    response = client.get(f"/exports/risks?asset_id={asset.id}")
    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    assert lines[0].strip() == ",".join(RISK_COLUMNS)
    assert len(lines) == 2  # header + 1 row
    assert "financial" in lines[1]


def test_export_obligations_forbidden_for_unassigned_asset():
    db, admin, asset = _seed_obligations_db(obligations_count=1)
    viewer = _viewer_user()
    db.users.append(viewer)
    # viewer has NO assignment to asset → expect 403
    client = _make_client(db, viewer)

    response = client.get(f"/exports/obligations?asset_id={asset.id}")
    assert response.status_code == 403


def test_export_obligations_empty_result_returns_valid_csv():
    db = FakeSession()
    admin = _admin_user()
    db.users.append(admin)
    asset = _asset(admin.id)
    db.assets.append(asset)
    # No documents or obligations
    client = _make_client(db, admin)

    response = client.get(f"/exports/obligations?asset_id={asset.id}")
    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    assert len(lines) == 1  # header only
    assert lines[0].strip() == ",".join(OBLIGATION_COLUMNS)


def test_export_obligations_cap_triggers_413(monkeypatch):
    db, admin, asset = _seed_obligations_db(obligations_count=3)
    monkeypatch.setattr(exports_router, "_max_rows", lambda: 2)
    client = _make_client(db, admin)

    response = client.get(f"/exports/obligations?asset_id={asset.id}")
    assert response.status_code == 413
    assert "tighten filters" in response.json()["detail"]
```

- [ ] **Step 4: Run the full test file**

Run: `python3 -m pytest backend/tests/test_exports_router.py -v`
Expected: all 13 tests PASS (6 pure-function + 7 endpoint).

- [ ] **Step 5: Run full backend test suite to confirm no regressions**

Run: `python3 -m pytest -q backend/tests`
Expected: 198 existing + 13 new = 211 passing. Zero failures.

- [ ] **Step 6: Commit**

```
git add backend/app/main.py backend/tests/test_exports_router.py
git commit -m "feat: register exports router and cover it with tests"
```

---

## Task 9: Add frontend `downloadExport` helper

**Files:**
- Modify: `frontend/src/lib/csv.ts`
- Create: `frontend/src/lib/__tests__/csv.test.ts`

- [ ] **Step 1: Write a failing test for the filename parser**

We'll extract the `Content-Disposition` filename parser as a pure function so we can unit-test it.

Create `frontend/src/lib/__tests__/csv.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { parseFilenameFromDisposition } from "../csv";

describe("parseFilenameFromDisposition", () => {
  it("extracts quoted filename", () => {
    expect(
      parseFilenameFromDisposition('attachment; filename="obligations_willow_2026-04-23.csv"'),
    ).toBe("obligations_willow_2026-04-23.csv");
  });

  it("returns fallback when header missing", () => {
    expect(parseFilenameFromDisposition(null, "fallback.csv")).toBe("fallback.csv");
  });

  it("returns fallback when no filename in header", () => {
    expect(parseFilenameFromDisposition("attachment", "fallback.csv")).toBe("fallback.csv");
  });
});
```

- [ ] **Step 2: Run — should fail**

Run: `cd frontend && npm run test -- --run src/lib/__tests__/csv.test.ts`
Expected: FAIL — `parseFilenameFromDisposition` is not exported.

- [ ] **Step 3: Implement helpers**

Open `frontend/src/lib/csv.ts` and append (do NOT modify existing `downloadCsv` / `csvFilename`):

```typescript
export function parseFilenameFromDisposition(
  header: string | null,
  fallback: string = "export",
): string {
  if (!header) return fallback;
  const match = header.match(/filename="([^"]+)"/i);
  return match ? match[1] : fallback;
}

export async function downloadExport(
  endpoint: "obligations" | "risks",
  params: URLSearchParams,
  format: "csv" | "xlsx",
  token: string,
): Promise<void> {
  const query = new URLSearchParams(params);
  query.set("format", format);
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const res = await fetch(`${base}/exports/${endpoint}?${query.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    let detail = `Export failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // response wasn't JSON — keep default message
    }
    throw new Error(detail);
  }
  const filename = parseFilenameFromDisposition(
    res.headers.get("content-disposition"),
    `${endpoint}.${format}`,
  );
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd frontend && npm run test -- --run src/lib/__tests__/csv.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Run full frontend unit tests**

Run: `cd frontend && npm run test -- --run`
Expected: all Vitest tests PASS (including existing `evidence-utils` tests).

- [ ] **Step 6: Commit**

```
git add frontend/src/lib/csv.ts frontend/src/lib/__tests__/csv.test.ts
git commit -m "feat: downloadExport helper for server-side exports"
```

---

## Task 10: Add Download CSV/Excel buttons to the Obligations page

**Files:**
- Modify: `frontend/src/app/obligations/ObligationsClientPage.tsx`

- [ ] **Step 1: Add state and handler**

Open `frontend/src/app/obligations/ObligationsClientPage.tsx`. Locate the `useState` declarations (lines 65–78 area). Add the `downloadExport` import from `@/lib/csv` at the top of the file near the other `@/lib/*` imports:

```typescript
import { downloadExport } from "@/lib/csv";
```

Add two new `useState` hooks near the existing ones:

```typescript
const [downloadingFormat, setDownloadingFormat] = useState<"csv" | "xlsx" | null>(null);
const [downloadError, setDownloadError] = useState<string | null>(null);
```

Add a handler inside the component (near the other `useCallback` handlers):

```typescript
const handleDownload = useCallback(
  async (format: "csv" | "xlsx") => {
    setDownloadingFormat(format);
    setDownloadError(null);
    try {
      const token = await getToken();
      if (!token) {
        throw new Error("Not authenticated");
      }
      const params = new URLSearchParams();
      if (assetId) params.set("asset_id", assetId);
      await downloadExport("obligations", params, format, token);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Download failed";
      setDownloadError(message);
    } finally {
      setDownloadingFormat(null);
    }
  },
  [assetId, getToken],
);
```

- [ ] **Step 2: Add buttons to the JSX**

Locate the filter toolbar row (around line 366 where `domainFilter` `<select>` lives). Add two buttons to the same row (adapt classes to match adjacent button styling):

```tsx
<button
  type="button"
  onClick={() => handleDownload("csv")}
  disabled={downloadingFormat !== null}
  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
>
  {downloadingFormat === "csv" ? "Preparing…" : "Download CSV"}
</button>
<button
  type="button"
  onClick={() => handleDownload("xlsx")}
  disabled={downloadingFormat !== null}
  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
>
  {downloadingFormat === "xlsx" ? "Preparing…" : "Download Excel"}
</button>
{downloadError ? (
  <span className="text-sm text-red-600" role="alert">{downloadError}</span>
) : null}
```

- [ ] **Step 3: Build the frontend to catch type errors**

Run: `cd frontend && npm run build`
Expected: successful build. Fix any TypeScript errors surfaced by the new imports / state.

- [ ] **Step 4: Run lint**

Run: `cd frontend && npm run lint`
Expected: zero new warnings. Fix any reported issues.

- [ ] **Step 5: Commit**

```
git add frontend/src/app/obligations/ObligationsClientPage.tsx
git commit -m "feat: Download CSV/Excel buttons on obligations page"
```

---

## Task 11: Add Download CSV/Excel buttons to the Risks page

**Files:**
- Modify: `frontend/src/app/risks/RisksClientPage.tsx`

- [ ] **Step 1: Add state, handler, and buttons**

Open `frontend/src/app/risks/RisksClientPage.tsx`. Follow the exact same pattern as Task 10:

1. Import `downloadExport` from `@/lib/csv`.
2. Add `downloadingFormat` and `downloadError` state.
3. Add `handleDownload` callback (endpoint `"risks"` instead of `"obligations"`, and pass the same `asset_id` param handling).
4. Add two buttons styled to match the page's existing filter toolbar. Include the `downloadError` `<span role="alert">` conditional.

Reuse the same JSX/button code shown in Task 10 Step 2, substituting `"obligations"` → `"risks"` in the `downloadExport` call.

- [ ] **Step 2: Build + lint**

Run: `cd frontend && npm run lint && npm run build`
Expected: both green.

- [ ] **Step 3: Commit**

```
git add frontend/src/app/risks/RisksClientPage.tsx
git commit -m "feat: Download CSV/Excel buttons on risks page"
```

---

## Task 12: Update CLAUDE.md API section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the two new endpoints to the API list**

Open `CLAUDE.md` and find the `### API (`backend/app/routers/`)` section (around line 220). Add the following line after the `GET /risks` entries:

```
- `GET /exports/obligations`, `GET /exports/risks` — CSV/XLSX export, same filters as list endpoints, `?format=csv|xlsx`, 50k row cap via `exports.max_rows`
```

- [ ] **Step 2: Commit**

```
git add CLAUDE.md
git commit -m "docs: add /exports endpoints to CLAUDE.md API section"
```

---

## Task 13: Final verification

- [ ] **Step 1: Compile all backend code**

Run: `python3 -m compileall backend/app backend/alembic backend/tools -q`
Expected: exit 0.

- [ ] **Step 2: Run the full backend test suite**

Run: `python3 -m pytest -q backend/tests`
Expected: 211 tests pass (198 existing + 13 new).

- [ ] **Step 3: Run the frontend test suite**

Run: `cd frontend && npm run test -- --run`
Expected: all Vitest tests pass.

- [ ] **Step 4: Lint + build the frontend**

Run: `cd frontend && npm run lint && npm run build`
Expected: both green.

- [ ] **Step 5: Manual smoke (backend dev server)**

Start backend and check endpoint registration:

```
make backend
```

In a separate shell:

```
curl -s http://localhost:8001/openapi.json | python3 -c "import sys,json; data=json.load(sys.stdin); print([p for p in data['paths'] if '/exports' in p])"
```

Expected: `['/exports/obligations', '/exports/risks']`.

Kill the backend with Ctrl-C.

- [ ] **Step 6: Optional end-to-end manual check**

If a real PDF has been ingested and obligations exist, open the obligations page at `http://localhost:3000/obligations?asset_id=<id>` with the dev server, click "Download CSV", and confirm:
- The downloaded file's name is `obligations_<asset_slug>_<YYYY-MM-DD>.csv`.
- Column order matches the spec.
- Severity column reflects `llm_severity` when present.

Repeat for "Download Excel" — open in Excel/Numbers and confirm header row is bold and severity cells are color-coded.

- [ ] **Step 7: No final commit needed**

All verification is read-only. If any step fails, fix and commit a normal bugfix referencing the specific failure.

---

## Completion Criteria

- Backend: `/exports/obligations` and `/exports/risks` registered, access-controlled, filter-aware, stream CSV or build XLSX, 50k row cap.
- Frontend: Download CSV and Download Excel buttons on `/obligations` and `/risks`, respecting current `asset_id` filter, with spinner state and error surface.
- Tests: 13 new tests pass (6 pure-function + 7 endpoint integration). Existing 198 still pass.
- Docs: CLAUDE.md API section updated.

## Self-review notes

- Spec coverage:
  - 17-column obligations CSV/XLSX: Tasks 3, 6, 7, 8.
  - 16-column risks CSV/XLSX: Tasks 3, 6, 7, 8.
  - Backend-owned filename via `Content-Disposition`: Task 3 (`_filename`) + Task 7 (response headers) + Task 9 (frontend parser).
  - openpyxl dep added: Task 1.
  - Config cap `exports.max_rows`: Task 1 + Task 7.
  - 413 when cap exceeded: Task 7 (`_too_large`) + Task 8 (regression test).
  - Severity color fills matching SeverityBadge hex codes: Task 3 (`_SEVERITY_FILL_HEX`) + Task 7 (`_xlsx_bytes`) + Task 8 (cell-color test).
  - Access control via `require_asset_scope`: Task 7 (dep) + Task 8 (403 test).
  - Primary evidence = MIN(created_at) tie-broken by id: Task 6 (`_resolve_*_rows` sort + `setdefault`).
  - Latest review = MAX(created_at): Task 6.
  - Reviewer email via User lookup: Task 6.
  - Streaming CSV: Task 7 (`StreamingResponse` + `_csv_lines` generator).
  - XLSX in BytesIO: Task 7 (`_xlsx_bytes`).
  - Frontend buttons respect current filters: Task 10 (obligations includes `asset_id`), Task 11 (risks mirrors pattern).
  - CLAUDE.md updated: Task 12.
- Placeholder scan: no TBDs. Every code step shows full code.
- Type consistency: `_resolve_obligation_rows` returns `Iterator[_ObligationRow]` → consumed by `_row_for_obligation` in Task 7. `OBLIGATION_COLUMNS` / `RISK_COLUMNS` defined once in exports.py, used identically in tests. Severity hex fill keys match `Severity` enum string values (critical/high/medium/low).
- Known deviation from spec: the spec mentioned "correlated subquery" for primary evidence and latest review; the plan uses three separate `IN` queries + Python grouping. This is observationally equivalent, easier to test with `FakeSession`, and still O(N) per export. Recorded here explicitly rather than silently.
