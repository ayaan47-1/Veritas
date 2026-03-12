from __future__ import annotations

from ..worker.celery_app import celery_app
from .tasks.parse import parse_document
from .tasks.ocr import ocr_scanned_pages
from .tasks.chunk import normalize_pages, chunk_pages
from .tasks.classify import classify_document
from .tasks.extract import extract_entities, extract_obligations, extract_risks
from .tasks.verify import verify_extractions
from .tasks.score import score_extractions
from .tasks.notify import persist_final_status, emit_notifications


@celery_app.task(name="process_document")
def process_document(document_id: str) -> None:
    """Orchestrator — runs the full pipeline (synchronous stub)."""
    parse_document(document_id)
    ocr_scanned_pages(document_id)
    normalize_pages(document_id)
    chunk_pages(document_id)
    classify_document(document_id)
    extract_entities(document_id)
    extract_obligations(document_id)
    extract_risks(document_id)
    verify_extractions(document_id)
    score_extractions(document_id)
    persist_final_status(document_id)
    emit_notifications(document_id)

