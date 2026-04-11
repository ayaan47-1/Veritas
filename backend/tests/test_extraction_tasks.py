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


FINANCIAL_DOMAINS = {
    "financial": {
        "doc_types": ["insurance_policy", "loan_agreement", "deed_of_trust"],
        "stage_keywords": {
            "obligation_extraction": ["premium", "repayment", "borrower", "lender"],
            "risk_extraction": ["default", "foreclosure", "exclusion", "lapse"],
            "entity_extraction": ["borrower", "lender", "insurer", "trustee"],
        },
        "obligation_aliases": {"delivery": "submission", "maintenance": "inspection"},
        "vocab_preambles": {
            "obligation_extraction": "This is a financial/insurance document.",
            "risk_extraction": "This is a financial/insurance document.",
        },
    },
    "general": {
        "doc_types": ["unknown"],
        "stage_keywords": {
            "obligation_extraction": ["shall", "must"],
            "risk_extraction": ["penalty", "breach"],
            "entity_extraction": ["party"],
        },
        "obligation_aliases": {},
        "vocab_preambles": {},
    },
}


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


def test_get_stage_keywords_returns_domain_keywords(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", types.SimpleNamespace(raw={"domains": FINANCIAL_DOMAINS}))
    keywords = extract_task._get_stage_keywords("obligation_extraction", DocumentType.loan_agreement)
    assert "premium" in keywords
    assert "borrower" in keywords


def test_get_stage_keywords_falls_back_to_general(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", types.SimpleNamespace(raw={"domains": FINANCIAL_DOMAINS}))
    keywords = extract_task._get_stage_keywords("obligation_extraction", DocumentType.unknown)
    assert "shall" in keywords


def test_get_obligation_aliases_returns_domain_aliases(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", types.SimpleNamespace(raw={"domains": FINANCIAL_DOMAINS}))
    aliases = extract_task._get_obligation_aliases(DocumentType.insurance_policy)
    assert aliases.get("delivery") == "submission"
    assert aliases.get("maintenance") == "inspection"


def test_vocab_preamble_injected_into_obligation_prompt(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", types.SimpleNamespace(raw={"domains": FINANCIAL_DOMAINS}))
    chunk = _make_chunk(uuid.uuid4(), 1, "The borrower shall repay the principal.")
    document = _make_document(DocumentType.loan_agreement)
    prompt = extract_task._build_extraction_prompt("obligation_extraction", chunk, document)
    assert "financial/insurance document" in prompt


def test_vocab_preamble_absent_for_unknown(monkeypatch):
    monkeypatch.setattr(extract_task, "settings", types.SimpleNamespace(raw={"domains": FINANCIAL_DOMAINS}))
    chunk = _make_chunk(uuid.uuid4(), 1, "Some text.")
    document = _make_document(DocumentType.unknown)
    prompt = extract_task._build_extraction_prompt("obligation_extraction", chunk, document)
    assert "financial/insurance document" not in prompt


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


def _settings_no_grouping():
    return types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1,
                "chunk_selection": {"chunks_per_group": 1}},
        "domains": {},
        "extraction": {"mode": "chunked"},
    })


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

    monkeypatch.setattr(extract_task, "settings", _settings_no_grouping())
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

    monkeypatch.setattr(extract_task, "settings", _settings_no_grouping())
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

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1,
                "chunk_selection": {"chunks_per_group": 1}},
        "domains": {
            "construction": {
                "doc_types": ["contract"],
                "obligation_aliases": {"delivery": "submission", "maintenance": "inspection"},
                "stage_keywords": {}, "vocab_preambles": {},
            },
        },
        "extraction": {"mode": "chunked"},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
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
                "chunk_selection": {"chunks_per_group": 1},
            },
            "domains": {},
            "extraction": {"mode": "chunked"},
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
                "chunk_selection": {"max_chunks_per_stage": 1, "use_mmr": False, "chunks_per_group": 1},
            },
            "extraction": {"mode": "chunked"},
        }
    )

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_risks(document.id)

    assert len(db.risks) == 1


def test_extract_obligations_deduplicates_overlapping_quotes_and_keeps_richer_parse(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Chunk one."),
        _make_chunk(document.id, 1, "Chunk two overlap."),
    ]
    contractor = Entity(
        id=uuid.uuid4(),
        canonical_name="Contractor",
        entity_type=EntityType.party,
        aliases=[],
    )
    db = FakeSession(document=document, chunks=chunks, entities=[contractor])

    def _fake_llm(*, model: str, prompt: str, stage: str):
        if "Chunk one" in prompt:
            return [
                {
                    "quote": "Contractor shall pay the lender by 2026-06-15.",
                    "obligation_type": "payment",
                    "modality": "shall",
                    "due_date": None,
                    "due_rule": None,
                    "severity": "medium",
                    "responsible_party": None,
                }
            ]
        return [
            {
                "quote": "The Contractor shall pay the lender by 2026-06-15.",
                "obligation_type": "payment",
                "modality": "shall",
                "due_date": "2026-06-15",
                "due_rule": None,
                "severity": "high",
                "responsible_party": "Contractor",
            }
        ]

    monkeypatch.setattr(extract_task, "settings", _settings_no_grouping())
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    assert len(db.obligations) == 1
    obligation = db.obligations[0]
    assert obligation.obligation_type == ObligationType.payment
    assert obligation.due_kind == DueKind.absolute
    assert str(obligation.due_date) == "2026-06-15"
    assert obligation.responsible_entity_id == contractor.id


def test_extract_risks_deduplicates_overlapping_quotes_and_keeps_best_parse(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Chunk alpha."),
        _make_chunk(document.id, 1, "Chunk beta overlap."),
    ]
    db = FakeSession(document=document, chunks=chunks)

    def _fake_llm(*, model: str, prompt: str, stage: str):
        if "Chunk alpha" in prompt:
            return [{"quote": "Borrower default may trigger foreclosure.", "risk_type": "unknown_risk", "severity": "low"}]
        return [{"quote": "Borrower default may trigger foreclosure. ", "risk_type": "contractual", "severity": "high"}]

    monkeypatch.setattr(extract_task, "settings", _settings_no_grouping())
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_risks(document.id)

    assert len(db.risks) == 1
    risk = db.risks[0]
    assert risk.risk_type == RiskType.contractual
    assert risk.severity == Severity.high


# ---------------------------------------------------------------------------
# New tests for grouped chunk extraction + prompt changes
# ---------------------------------------------------------------------------


def test_group_chunks_creates_correct_groups():
    doc_id = uuid.uuid4()
    chunks = [_make_chunk(doc_id, i, f"text {i}") for i in range(7)]
    groups = extract_task._group_chunks(chunks, 3)
    assert len(groups) == 3
    assert len(groups[0]) == 3
    assert len(groups[1]) == 3
    assert len(groups[2]) == 1

    # group_size <= 1 produces one chunk per group
    singles = extract_task._group_chunks(chunks, 1)
    assert len(singles) == 7
    assert all(len(g) == 1 for g in singles)


def test_build_grouped_prompt_contains_all_chunks():
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Alpha clause text."),
        _make_chunk(document.id, 2, "Beta clause text."),
        _make_chunk(document.id, 3, "Gamma clause text."),
    ]
    prompt = extract_task._build_grouped_extraction_prompt(
        "obligation_extraction", chunks, document,
    )
    assert "Alpha clause text." in prompt
    assert "Beta clause text." in prompt
    assert "Gamma clause text." in prompt
    assert "--- Page 1 ---" in prompt
    assert "--- Page 2 ---" in prompt
    assert "--- Page 3 ---" in prompt
    assert "Pages: 1\u20133" in prompt
    assert "Do NOT duplicate items" in prompt


def test_grouped_extraction_reduces_llm_calls(monkeypatch):
    document = _make_document()
    chunks = [_make_chunk(document.id, i, f"Chunk {i} shall comply.") for i in range(6)]
    db = FakeSession(document=document, chunks=chunks, entities=[])

    call_count = [0]

    def _fake_llm(*, model: str, prompt: str, stage: str):
        call_count[0] += 1
        return [{"quote": "shall comply", "obligation_type": "compliance",
                 "modality": "shall", "severity": "medium",
                 "due_date": None, "due_rule": None, "responsible_party": None}]

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1,
                "chunk_selection": {"chunks_per_group": 3}},
        "domains": {},
        "extraction": {"mode": "chunked"},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    # 6 chunks / group_size 3 = 2 LLM calls
    assert call_count[0] == 2


def test_grouped_extraction_falls_back_on_group_failure(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Risk penalty clause A."),
        _make_chunk(document.id, 2, "Risk penalty clause B."),
    ]
    db = FakeSession(document=document, chunks=chunks)

    call_count = [0]

    def _fake_llm(*, model: str, prompt: str, stage: str):
        call_count[0] += 1
        # Grouped prompt contains "--- Page" header; fail it
        if "--- Page" in prompt:
            raise RuntimeError("group too large")
        # Per-chunk fallback succeeds
        return [{"quote": "penalty clause", "risk_type": "financial", "severity": "high"}]

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1,
                "chunk_selection": {"chunks_per_group": 5}},
        "domains": {},
        "extraction": {"mode": "chunked"},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_risks(document.id)

    # Group call failed (1 call) → per-chunk fallback (2 calls) = 3 total
    assert call_count[0] == 3
    assert len(db.risks) >= 1


def test_prompt_no_longer_says_err_on_inclusion():
    assert "err on the side of inclusion" not in extract_task._OBLIGATION_SCHEMA
    assert "err on the side of inclusion" not in extract_task._RISK_SCHEMA


# ---------------------------------------------------------------------------
# Full-document extraction tests
# ---------------------------------------------------------------------------


def test_estimate_token_count():
    doc_id = uuid.uuid4()
    chunks = [
        _make_chunk(doc_id, 1, "a" * 400),
        _make_chunk(doc_id, 2, "b" * 400),
        _make_chunk(doc_id, 3, "c" * 400),
    ]
    assert extract_task._estimate_token_count(chunks, 4) == 300
    assert extract_task._estimate_token_count(chunks, 2) == 600


def test_should_use_full_doc_modes():
    doc_id = uuid.uuid4()
    chunks = [_make_chunk(doc_id, 1, "x" * 100)]

    assert extract_task._should_use_full_doc(chunks, {"mode": "chunked"}) is False
    assert extract_task._should_use_full_doc(chunks, {"mode": "full_doc"}) is True

    # auto: 100 chars / 4 = 25 tokens + 1500 overhead = 1525 < 150000 → True
    assert extract_task._should_use_full_doc(chunks, {"mode": "auto"}) is True

    # auto with tiny threshold: 1525 > 10 → False
    assert extract_task._should_use_full_doc(
        chunks, {"mode": "auto", "full_doc_token_threshold": 10}
    ) is False


def test_full_doc_mode_sends_single_call(monkeypatch):
    document = _make_document()
    chunks = [_make_chunk(document.id, i, f"Chunk {i} shall comply.") for i in range(6)]
    db = FakeSession(document=document, chunks=chunks, entities=[])

    call_count = [0]
    prompts_seen = []

    def _fake_llm(*, model: str, prompt: str, stage: str):
        call_count[0] += 1
        prompts_seen.append(prompt)
        return [{"quote": "shall comply", "obligation_type": "compliance",
                 "modality": "shall", "severity": "medium",
                 "due_date": None, "due_rule": None, "responsible_party": None}]

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1},
        "domains": {},
        "extraction": {"mode": "full_doc"},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    assert call_count[0] == 1
    # All chunk texts should appear in the single prompt
    for i in range(6):
        assert f"Chunk {i}" in prompts_seen[0]


def test_auto_mode_selects_full_doc_for_small_docs(monkeypatch):
    document = _make_document()
    chunks = [_make_chunk(document.id, 1, "Small doc text.")]
    db = FakeSession(document=document, chunks=chunks, entities=[])

    call_count = [0]

    def _fake_llm(*, model: str, prompt: str, stage: str):
        call_count[0] += 1
        return [{"quote": "Small doc text.", "obligation_type": "compliance",
                 "modality": "shall", "severity": "medium",
                 "due_date": None, "due_rule": None, "responsible_party": None}]

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1},
        "domains": {},
        "extraction": {"mode": "auto", "full_doc_token_threshold": 100000},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    # Small doc → full-doc → 1 LLM call
    assert call_count[0] == 1


def test_auto_mode_selects_chunked_for_large_docs(monkeypatch):
    document = _make_document()
    chunks = [_make_chunk(document.id, i, f"Chunk {i} text.") for i in range(3)]
    db = FakeSession(document=document, chunks=chunks, entities=[])

    call_count = [0]

    def _fake_llm(*, model: str, prompt: str, stage: str):
        call_count[0] += 1
        return [{"quote": "text", "obligation_type": "compliance",
                 "modality": "shall", "severity": "medium",
                 "due_date": None, "due_rule": None, "responsible_party": None}]

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1,
                "chunk_selection": {"chunks_per_group": 1}},
        "domains": {},
        "extraction": {"mode": "auto", "full_doc_token_threshold": 1},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    # Threshold=1 → chunked → multiple calls (one per chunk)
    assert call_count[0] == 3


def test_full_doc_falls_back_to_chunked_on_failure(monkeypatch):
    document = _make_document()
    chunks = [
        _make_chunk(document.id, 1, "Obligation clause A."),
        _make_chunk(document.id, 2, "Obligation clause B."),
    ]
    db = FakeSession(document=document, chunks=chunks, entities=[])

    call_count = [0]

    def _fake_llm(*, model: str, prompt: str, stage: str):
        call_count[0] += 1
        # Full-doc prompt has all chunks → contains "--- Page" headers
        if "--- Page 1 ---" in prompt and "--- Page 2 ---" in prompt:
            raise RuntimeError("context too long")
        # Per-chunk fallback succeeds
        return [{"quote": "clause", "obligation_type": "compliance",
                 "modality": "shall", "severity": "medium",
                 "due_date": None, "due_rule": None, "responsible_party": None}]

    fake_settings = types.SimpleNamespace(raw={
        "llm": {"primary_model": "test-model", "fallback_models": [],
                "max_retries": 1, "retry_backoff_base": 1,
                "chunk_selection": {"chunks_per_group": 1}},
        "domains": {},
        "extraction": {"mode": "full_doc"},
    })

    monkeypatch.setattr(extract_task, "settings", fake_settings)
    monkeypatch.setattr(extract_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(extract_task, "update_parse_status", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_task, "call_extract_llm", _fake_llm)
    monkeypatch.setattr(extract_task.time, "sleep", lambda *_a, **_k: None)

    extract_task.extract_obligations(document.id)

    # Full-doc failed (1 call) + chunked fallback (2 calls) = 3
    assert call_count[0] == 3
    assert len(db.obligations) >= 1
