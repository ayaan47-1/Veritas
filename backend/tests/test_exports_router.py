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
