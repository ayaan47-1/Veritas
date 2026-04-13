from __future__ import annotations

import hashlib
import json
import types
import uuid

from backend.app.models import (
    Document,
    DocumentPage,
    DocumentType,
    DueKind,
    Modality,
    Obligation,
    ObligationType,
    ParseStatus,
    PromptVersion,
    ReviewStatus,
    Risk,
    RiskType,
    Severity,
    TextSource,
)
from backend.app.worker.tasks import critic as critic_task


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self._session = session
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return [row for row in self._rows_for_model() if self._matches_all(row)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def _rows_for_model(self):
        if self._model is critic_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is critic_task.DocumentPage:
            return list(self._session.pages)
        if self._model is critic_task.Obligation:
            return list(self._session.obligations)
        if self._model is critic_task.Risk:
            return list(self._session.risks)
        if self._model is critic_task.Entity:
            return list(self._session.entities)
        if self._model is critic_task.PromptVersion:
            return list(self._session.prompt_versions)
        if self._model is critic_task.ExtractionRun:
            return list(self._session.extraction_runs)
        return []

    def _matches_all(self, row):
        return all(self._matches(row, condition) for condition in self._conditions)

    def _matches(self, row, condition):
        left = getattr(condition, "left", None)
        right = getattr(condition, "right", None)
        if left is None or right is None:
            return True
        key = getattr(left, "key", None)
        if key is None:
            return True
        value = getattr(right, "value", right)
        return getattr(row, key) == value


class FakeSession:
    def __init__(
        self,
        *,
        document: Document,
        pages: list[DocumentPage] | None = None,
        obligations: list[Obligation] | None = None,
        risks: list[Risk] | None = None,
    ):
        self.document = document
        self.pages = pages or []
        self.obligations = obligations or []
        self.risks = risks or []
        self.entities = []
        self.prompt_versions: list[PromptVersion] = []
        self.extraction_runs = []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, critic_task.Document):
            self.document = obj
            return
        if isinstance(obj, critic_task.PromptVersion) and obj not in self.prompt_versions:
            self.prompt_versions.append(obj)
            return
        if isinstance(obj, critic_task.ExtractionRun) and obj not in self.extraction_runs:
            self.extraction_runs.append(obj)
            return
        if isinstance(obj, critic_task.Obligation) and obj not in self.obligations:
            self.obligations.append(obj)
            return
        if isinstance(obj, critic_task.Risk) and obj not in self.risks:
            self.risks.append(obj)
            return

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _make_document() -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.verification,
        doc_type=DocumentType.contract,
        scanned_page_count=0,
    )


def _make_page(document_id: uuid.UUID, text: str) -> DocumentPage:
    return DocumentPage(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=1,
        raw_text=text,
        normalized_text=text,
        text_source=TextSource.pdf_text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _make_obligation(document_id: uuid.UUID, text: str) -> Obligation:
    return Obligation(
        id=uuid.uuid4(),
        document_id=document_id,
        obligation_type=ObligationType.payment,
        obligation_text=text,
        modality=Modality.shall,
        responsible_entity_id=None,
        due_kind=DueKind.none,
        due_date=None,
        due_rule=None,
        trigger_date=None,
        severity=Severity.medium,
        status=ReviewStatus.needs_review,
        system_confidence=60,
        reviewer_confidence=None,
        has_external_reference=False,
        contradiction_flag=False,
        extraction_run_id=None,
        llm_severity=None,
        llm_quality_confidence=None,
        critic_valid=None,
        critic_confidence=None,
        critic_reasoning=None,
    )


def test_critic_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(critic_task, "settings", types.SimpleNamespace(raw={"critic": {"enabled": False}}))
    result = critic_task.criticize_extractions(str(uuid.uuid4()))
    assert result["status"] == "skipped"
    assert result["reason"] == "disabled"


def test_critic_auto_rejects_invalid_high_confidence(monkeypatch):
    document = _make_document()
    page = _make_page(document.id, "Contractor shall pay the invoice in full.")
    obligation = _make_obligation(document.id, "Contractor shall pay the invoice in full.")
    db = FakeSession(document=document, pages=[page], obligations=[obligation], risks=[])

    fake_settings = types.SimpleNamespace(raw={"critic": {"enabled": True, "model": "test-model", "max_items_per_call": 30, "auto_reject_threshold": 70}})

    def _fake_llm(model: str, prompt: str, prefer_json_object: bool = True) -> str:
        return json.dumps(
            {
                "validations": [
                    {"id": str(obligation.id), "valid": False, "confidence": 92, "reasoning": "Statutory summary, not agreement duty."}
                ],
                "new_obligations": [],
                "new_risks": [],
            }
        )

    monkeypatch.setattr(critic_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(critic_task, "settings", fake_settings)
    monkeypatch.setattr(critic_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(critic_task, "llm_completion", _fake_llm)
    monkeypatch.setattr(critic_task, "_verify_obligations", lambda *_a, **_k: ({}, {}))
    monkeypatch.setattr(critic_task, "_verify_risks", lambda *_a, **_k: ({}, {}))

    result = critic_task.criticize_extractions(str(document.id))

    assert result["status"] == "ok"
    assert result["validated_count"] == 1
    assert result["auto_rejected_count"] == 1
    assert obligation.critic_valid is False
    assert obligation.critic_confidence == 92
    assert obligation.status == ReviewStatus.rejected
    assert len(db.extraction_runs) == 1
    assert db.extraction_runs[0].stage == critic_task.ExtractionStage.critic_detection


def test_critic_adds_new_items_and_verifies(monkeypatch):
    document = _make_document()
    page = _make_page(document.id, "Borrower shall maintain insurance. Default triggers foreclosure.")
    db = FakeSession(document=document, pages=[page], obligations=[], risks=[])
    verify_calls = {"obligations": 0, "risks": 0}

    fake_settings = types.SimpleNamespace(raw={"critic": {"enabled": True, "model": "test-model", "max_items_per_call": 30, "auto_reject_threshold": 70}})

    def _fake_llm(model: str, prompt: str, prefer_json_object: bool = True) -> str:
        return json.dumps(
            {
                "validations": [],
                "new_obligations": [
                    {
                        "quote": "Borrower shall maintain insurance.",
                        "obligation_type": "compliance",
                        "modality": "shall",
                        "severity": "high",
                        "due_date": None,
                        "due_rule": None,
                        "responsible_party": None,
                    }
                ],
                "new_risks": [
                    {
                        "quote": "Default triggers foreclosure.",
                        "risk_type": "contractual",
                        "severity": "high",
                    }
                ],
            }
        )

    def _fake_verify_obligations(db_sess, doc, pages, rows):
        verify_calls["obligations"] += len(rows)
        return {}, {}

    def _fake_verify_risks(db_sess, doc, pages, rows):
        verify_calls["risks"] += len(rows)
        return {}, {}

    monkeypatch.setattr(critic_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(critic_task, "settings", fake_settings)
    monkeypatch.setattr(critic_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(critic_task, "llm_completion", _fake_llm)
    monkeypatch.setattr(critic_task, "_verify_obligations", _fake_verify_obligations)
    monkeypatch.setattr(critic_task, "_verify_risks", _fake_verify_risks)

    result = critic_task.criticize_extractions(str(document.id))

    assert result["status"] == "ok"
    assert result["new_obligation_count"] == 1
    assert result["new_risk_count"] == 1
    assert verify_calls["obligations"] == 1
    assert verify_calls["risks"] == 1
    assert db.obligations[0].critic_valid is True
    assert db.risks[0].critic_valid is True
    run = db.extraction_runs[0]
    assert db.obligations[0].extraction_run_id == run.id
    assert db.risks[0].extraction_run_id == run.id
