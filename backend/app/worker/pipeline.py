from __future__ import annotations

import inngest

from .inngest_client import inngest_client
from .tasks.parse import parse_document
from .tasks.ocr import ocr_scanned_pages
from .tasks.chunk import normalize_pages, chunk_pages
from .tasks.classify import classify_document
from .tasks.extract import extract_entities, extract_obligations, extract_risks
from .tasks.verify import verify_extractions
from .tasks.score import score_extractions
from .tasks.rescore import rescore_with_llm
from .tasks.notify import persist_final_status, emit_notifications


@inngest_client.create_function(
    fn_id="process-document",
    trigger=inngest.TriggerEvent(event="veritas/document.uploaded"),
    retries=2,
)
async def process_document(
    ctx: inngest.Context,
) -> None:
    """Orchestrator — runs the full 11-stage pipeline with per-step tracking."""
    document_id: str = ctx.event.data["document_id"]
    step = ctx.step

    await step.run("1-parse", lambda: parse_document(document_id))
    await step.run("2-ocr", lambda: ocr_scanned_pages(document_id))
    await step.run("3-normalize", lambda: normalize_pages(document_id))
    await step.run("4-chunk", lambda: chunk_pages(document_id))
    await step.run("5-classify", lambda: classify_document(document_id))
    await step.run("6-extract-entities", lambda: extract_entities(document_id))
    await step.run("7-extract-obligations", lambda: extract_obligations(document_id))
    await step.run("8-extract-risks", lambda: extract_risks(document_id))
    await step.run("9-verify", lambda: verify_extractions(document_id))
    await step.run("10-score", lambda: score_extractions(document_id))
    await step.run("10b-rescore", lambda: rescore_with_llm(document_id))
    await step.run("11-persist", lambda: persist_final_status(document_id))
    await step.run("12-notify", lambda: emit_notifications(document_id))
