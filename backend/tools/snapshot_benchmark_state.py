"""Snapshot extraction state for benchmark documents.

Captures per-document counts of obligations/risks by status, plus the
extraction_runs history (stage, model, timestamp) for the most recent runs.
Used to preserve a baseline before running A/B tests that rerun stages.

Usage:
    python3 -m backend.tools.snapshot_benchmark_state \\
        --output backend/data/benchmarks/baseline_haiku_YYYYMMDD.json \\
        --document-ids 3c429f92-... 84b546df-... 996ae31d-...
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func

from backend.app.database import SessionLocal
from backend.app.models import Chunk, Document, ExtractionRun, Obligation, Risk

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _snapshot_document(db, doc_id: uuid.UUID) -> dict:
    document = db.query(Document).filter(Document.id == doc_id).first()
    if not document:
        return {"document_id": str(doc_id), "status": "not_found"}

    ob_by_status = dict(
        db.query(Obligation.status, func.count(Obligation.id))
        .filter(Obligation.document_id == doc_id)
        .group_by(Obligation.status)
        .all()
    )
    ri_by_status = dict(
        db.query(Risk.status, func.count(Risk.id))
        .filter(Risk.document_id == doc_id)
        .group_by(Risk.status)
        .all()
    )

    chunk_labels = dict(
        db.query(Chunk.section_label, func.count(Chunk.id))
        .filter(Chunk.document_id == doc_id)
        .group_by(Chunk.section_label)
        .all()
    )

    runs = (
        db.query(ExtractionRun)
        .filter(ExtractionRun.document_id == doc_id)
        .order_by(ExtractionRun.started_at.desc())
        .limit(30)
        .all()
    )
    runs_serialized = [
        {
            "stage": r.stage.value if hasattr(r.stage, "value") else str(r.stage),
            "model_used": r.model_used,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]

    return {
        "document_id": str(doc_id),
        "source_name": document.source_name,
        "doc_type": document.doc_type.value if document.doc_type else None,
        "obligations_by_status": {str(k): v for k, v in ob_by_status.items()},
        "risks_by_status": {str(k): v for k, v in ri_by_status.items()},
        "chunk_section_labels": {str(k): v for k, v in chunk_labels.items()},
        "extraction_runs": runs_serialized,
    }


def snapshot(document_ids: list[str], output_path: Path) -> dict:
    db = SessionLocal()
    try:
        docs = []
        for raw in document_ids:
            try:
                doc_id = uuid.UUID(raw)
            except ValueError:
                logger.warning("Skipping invalid UUID %s", raw)
                continue
            docs.append(_snapshot_document(db, doc_id))
    finally:
        db.close()

    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "documents": docs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str))
    logger.info("Snapshot written to %s (%d documents)", output_path, len(docs))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot benchmark document extraction state")
    parser.add_argument("--document-ids", nargs="+", required=True, help="Document UUIDs to snapshot")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()
    snapshot(args.document_ids, Path(args.output))


if __name__ == "__main__":
    main()
