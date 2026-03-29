"""Re-run extraction stages (6–10b) on an already-processed document.

Useful after changing config (e.g. max_chunks_per_stage, scoring weights)
without re-uploading the file.

Usage:
    python3 -m backend.tools.rerun_extraction --document-id <uuid>

Stages re-run in order:
    6  extract_entities
    7  extract_obligations
    8  extract_risks
    9  verify_extractions
    10 score_extractions
    10b rescore_with_llm  (skipped if rescoring.enabled=false in config)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.worker.tasks.extract import extract_entities, extract_obligations, extract_risks
from backend.app.worker.tasks.verify import verify_extractions
from backend.app.worker.tasks.score import score_extractions
from backend.app.worker.tasks.rescore import rescore_with_llm

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def rerun(document_id: str) -> None:
    logger.info("Stage 6: extract_entities")
    extract_entities(document_id)

    logger.info("Stage 7: extract_obligations")
    extract_obligations(document_id)

    logger.info("Stage 8: extract_risks")
    extract_risks(document_id)

    logger.info("Stage 9: verify_extractions")
    verify_extractions(document_id)

    logger.info("Stage 10: score_extractions")
    score_extractions(document_id)

    logger.info("Stage 10b: rescore_with_llm")
    rescore_with_llm(document_id)

    logger.info("Done — re-extraction complete for %s", document_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run extraction stages on an existing document")
    parser.add_argument("--document-id", required=True, help="UUID of the document to re-extract")
    args = parser.parse_args()
    rerun(args.document_id)


if __name__ == "__main__":
    main()
