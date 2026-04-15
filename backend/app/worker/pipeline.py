from __future__ import annotations

import inngest

from .inngest_client import inngest_client
from .tasks.parse import parse_document
from .tasks.ocr import ocr_scanned_pages
from .tasks.chunk import normalize_pages, chunk_pages
from .tasks.classify import classify_document
from .tasks.section_classify import classify_chunk_sections
from .tasks.extract import extract_entities, extract_obligations_and_risks
from .tasks.verify import verify_extractions
from .tasks.critic import criticize_extractions
from .tasks.score import score_extractions
from .tasks.rescore import rescore_with_llm
from .tasks.notify import persist_final_status, emit_notifications
from .tasks.compliance import (
    execute_mcp,
    persist_results,
    emit_compliance_notification,
)


@inngest_client.create_function(
    fn_id="process-document",
    trigger=inngest.TriggerEvent(event="veritas/document.uploaded"),
    retries=2,
)
async def process_document(
    ctx: inngest.Context,
    step: inngest.Step,
) -> None:
    """Orchestrator — runs the full 11-stage pipeline with per-step tracking."""
    document_id: str = ctx.event.data["document_id"]

    await step.run("1-parse", lambda: parse_document(document_id))
    await step.run("2-ocr", lambda: ocr_scanned_pages(document_id))
    await step.run("3-normalize", lambda: normalize_pages(document_id))
    await step.run("4-chunk", lambda: chunk_pages(document_id))
    await step.run("5-classify", lambda: classify_document(document_id))
    await step.run("5b-section-classify", lambda: classify_chunk_sections(document_id))
    await step.run("6-extract-entities", lambda: extract_entities(document_id))
    await step.run("7-extract-classify", lambda: extract_obligations_and_risks(document_id))
    await step.run("9-verify", lambda: verify_extractions(document_id))
    await step.run("9a-critic", lambda: criticize_extractions(document_id))
    await step.run("10-score", lambda: score_extractions(document_id))
    await step.run("10b-rescore", lambda: rescore_with_llm(document_id))
    await step.run("11-persist", lambda: persist_final_status(document_id))
    await step.run("12-notify", lambda: emit_notifications(document_id))


@inngest_client.create_function(
    fn_id="run-compliance-check",
    trigger=inngest.TriggerEvent(event="veritas/compliance.requested"),
    retries=2,
)
async def run_compliance_check(
    ctx: inngest.Context,
    step: inngest.Step,
) -> None:
    """Run VeritasMCP compliance pipeline and persist results."""
    report_id: str = ctx.event.data["report_id"]

    await step.run("1-execute-mcp", lambda: execute_mcp(report_id))
    await step.run("2-persist-results", lambda: persist_results(report_id))
    await step.run("3-notify", lambda: emit_compliance_notification(report_id))
