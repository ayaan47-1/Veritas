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
    date_part = filename[len("obligations_willow_creek_"):-4]
    assert len(date_part) == 10
    assert date_part[4] == "-" and date_part[7] == "-"


def test_filename_all_when_no_asset():
    filename = exports_router._filename("risks", None, "xlsx")
    assert filename.startswith("risks_all_")
    assert filename.endswith(".xlsx")
