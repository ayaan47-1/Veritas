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
