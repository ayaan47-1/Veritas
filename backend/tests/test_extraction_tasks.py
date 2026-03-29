from __future__ import annotations

import hashlib
import sys
import types
import uuid



from backend.app.models import (
    Chunk,
    Document,
    DocumentType,
    DueKind,
    Entity,
    EntityMention,
    EntityType,
    ExtractionRun,
    ExtractionStage,
    ExtractionStatus,
    Obligation,
    ObligationType,
    ParseStatus,
    PromptVersion,
    Risk,
    RiskType,
    ReviewStatus,
    Severity,
    SplitReason,
)
from backend.app.worker.tasks import extract as extract_task


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
        if self._model is extract_task.Document:
            return [self._session.document] if self._session.document else []
        if self._model is extract_task.Chunk:
            return list(self._session.chunks)
        if self._model is extract_task.PromptVersion:
            return list(self._session.prompt_versions)
        if self._model is extract_task.ExtractionRun:
            return list(self._session.extraction_runs)
        if self._model is extract_task.Entity:
            return list(self._session.entities)
        if self._model is extract_task.EntityMention:
            return list(self._session.entity_mentions)
        if self._model is extract_task.Obligation:
            return list(self._session.obligations)
        if self._model is extract_task.Risk:
            return list(self._session.risks)
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
    def __init__(self, document: Document, chunks: list[Chunk], entities: list[Entity] | None = None):
        self.document = document
        self.chunks = chunks
        self.entities = entities or []
        self.prompt_versions: list[PromptVersion] = []
        self.extraction_runs: list[ExtractionRun] = []
        self.entity_mentions: list[EntityMention] = []
        self.obligations: list[Obligation] = []
        self.risks: list[Risk] = []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, Document):
            self.document = obj
            return
        if isinstance(obj, PromptVersion):
            if obj not in self.prompt_versions:
                self.prompt_versions.append(obj)
            return
        if isinstance(obj, ExtractionRun):
            if obj not in self.extraction_runs:
                self.extraction_runs.append(obj)
            return
        if isinstance(obj, EntityMention):
            if obj not in self.entity_mentions:
                self.entity_mentions.append(obj)
            return
        if isinstance(obj, Obligation):
            if obj not in self.obligations:
                self.obligations.append(obj)
            return
        if isinstance(obj, Risk):
            if obj not in self.risks:
                self.risks.append(obj)
            return

    def commit(self):
        return None

    def rollback(self):
        return None

    def flush(self):
        return None

    def close(self):
        return None


def _make_document(doc_type: DocumentType = DocumentType.contract) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        source_name="doc.pdf",
        file_path="/tmp/doc.pdf",
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime_type="application/pdf",
        uploaded_by=uuid.uuid4(),
        parse_status=ParseStatus.extraction,
        doc_type=doc_type,
        scanned_page_count=0,
    )


def _make_chunk(document_id: uuid.UUID, page: int, text: str) -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=page,
        char_start=0,
        char_end=len(text),
        text=text,
        chunk_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        split_reason=SplitReason.full_page,
    )


def test_risk_type_enum_has_prompt_categories():
    expected = {
        "financial",
        "schedule",
        "quality",
        "safety",
        "compliance",
        "contractual",
        "unknown_risk",
    }
    actual = {member.value for member in RiskType}
    assert actual == expected


def test_extract_entities_partial_failure_and_suggestions(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Chunk one includes ACME Construction LLC."),
        _make_chunk(document.id, 2, "Chunk two fails."),
    ]
    entity = Entity(
        id=uuid.uuid4(),
        canonical_name="ACME Construction LLC",
        entity_type=EntityType.org,
        aliases=[],
    )
    db = FakeSession(document=document, chunks=chunks, entities=[entity])

    def _fake_llm(*, model: str, prompt: str, stage: str):
        if "Chunk two" in prompt:
            raise RuntimeError("chunk failure")
        return [{"name": "ACME Construction LLC", "page_number": 1}]

    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_entities(document.id)

    assert len(db.entity_mentions) == 1
    mention = db.entity_mentions[0]
    assert mention.mentioned_name == "ACME Construction LLC"
    assert mention.suggested_entity_id == entity.id
    assert mention.document_id == document.id

    assert len(db.extraction_runs) == 1
    run = db.extraction_runs[0]
    assert run.stage == ExtractionStage.entity_extraction
    assert run.status == ExtractionStatus.completed


def test_extract_obligations_maps_fields_and_handles_partial_failure(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Contractor shall pay by 2026-06-15."),
        _make_chunk(document.id, 2, "This chunk errors."),
    ]
    contractor = Entity(
        id=uuid.uuid4(),
        canonical_name="Contractor",
        entity_type=EntityType.party,
        aliases=[],
    )
    db = FakeSession(document=document, chunks=chunks, entities=[contractor])

    def _fake_llm(*, model: str, prompt: str, stage: str):
        if "errors" in prompt:
            raise RuntimeError("failed chunk")
        return [
            {
                "quote": "Contractor shall pay by 2026-06-15.",
                "page_number": 1,
                "obligation_type": "payment",
                "modality": "shall",
                "due_date": "2026-06-15",
                "due_rule": None,
                "severity": "high",
                "responsible_party": "Contractor",
                "explanation": "payment clause",
            }
        ]

    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    assert len(db.obligations) == 1
    ob = db.obligations[0]
    assert ob.obligation_type == ObligationType.payment
    assert ob.due_kind == DueKind.absolute
    assert str(ob.due_date) == "2026-06-15"
    assert ob.responsible_entity_id == contractor.id
    assert ob.status == ReviewStatus.needs_review
    assert ob.system_confidence == 0

    assert len(db.extraction_runs) == 1
    assert db.extraction_runs[0].stage == ExtractionStage.obligation_extraction
    assert db.extraction_runs[0].status == ExtractionStatus.completed


def test_extract_obligations_maps_delivery_and_maintenance_aliases(monkeypatch):
    document = _make_document()
    chunks = [_make_chunk(document.id, 1, "Deliver reports and maintain records.")]
    db = FakeSession(document=document, chunks=chunks, entities=[])

    def _fake_llm(*, model: str, prompt: str, stage: str):
        return [
            {
                "quote": "Deliver reports weekly.",
                "obligation_type": "delivery",
                "modality": "shall",
                "severity": "medium",
                "due_date": None,
                "due_rule": "within 7 days",
                "responsible_party": None,
            },
            {
                "quote": "Maintain records on site.",
                "obligation_type": "maintenance",
                "modality": "must",
                "severity": "medium",
                "due_date": None,
                "due_rule": None,
                "responsible_party": None,
            },
        ]

    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    assert len(db.obligations) == 2
    assert db.obligations[0].obligation_type == ObligationType.submission
    assert db.obligations[1].obligation_type == ObligationType.inspection


def test_extract_risks_uses_stage_fallback_for_remaining_chunks(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Chunk alpha content."),
        _make_chunk(document.id, 2, "Chunk beta content."),
    ]
    db = FakeSession(document=document, chunks=chunks)

    calls: list[tuple[str, str]] = []

    def _fake_llm(*, model: str, prompt: str, stage: str):
        if "alpha" in prompt.lower() and model == "primary-model":
            calls.append((model, "alpha"))
            raise RuntimeError("primary down")
        if "alpha" in prompt.lower():
            calls.append((model, "alpha"))
            return [{"quote": "alpha quote", "risk_type": "financial", "severity": "medium", "explanation": "alpha"}]
        calls.append((model, "beta"))
        return [{"quote": "beta quote", "risk_type": "schedule", "severity": "high", "explanation": "beta"}]

    fake_settings = types.SimpleNamespace(
        raw={
            "llm": {
                "primary_model": "primary-model",
                "fallback_models": ["fallback-model"],
                "max_retries": 1,
                "retry_backoff_base": 1,
            }
        }
    )

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_risks(document.id)

    assert calls == [
        ("primary-model", "alpha"),
        ("fallback-model", "alpha"),
        ("fallback-model", "beta"),
    ]

    assert len(db.risks) == 2
    assert {r.risk_type for r in db.risks} == {RiskType.financial, RiskType.schedule}
    assert all(r.status == ReviewStatus.needs_review for r in db.risks)
    assert all(r.system_confidence == 0 for r in db.risks)

    assert len(db.extraction_runs) == 1
    run = db.extraction_runs[0]
    assert run.stage == ExtractionStage.risk_extraction
    assert run.model_used == "fallback-model"
    assert run.status == ExtractionStatus.completed


def test_extract_risks_honors_chunk_selection_limit(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Risk penalty delay default."),
        _make_chunk(document.id, 2, "Another risk with liability language."),
    ]
    db = FakeSession(document=document, chunks=chunks)

    def _fake_llm(*, model: str, prompt: str, stage: str):
        if "penalty" in prompt.lower():
            return [{"quote": "Risk A", "risk_type": "financial", "severity": "medium"}]
        return [{"quote": "Risk B", "risk_type": "schedule", "severity": "high"}]

    fake_settings = types.SimpleNamespace(
        raw={
            "llm": {
                "primary_model": "primary-model",
                "fallback_models": [],
                "max_retries": 1,
                "retry_backoff_base": 1,
                "chunk_selection": {"max_chunks_per_stage": 1, "use_mmr": False},
            }
        }
    )

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_risks(document.id)

    assert len(db.risks) == 1
