"""Resume a stuck document past stage 7 using its existing obligations/risks.

Use when stage 7 succeeded server-side but Inngest gave up (e.g. Cloudflare
524 timeout on a long-running step). By default, drops duplicate
obligations/risks from older retried extraction runs, then runs the remaining
downstream stages (verify, critic, score, rescore, persist, notify) in-process.
"""
from __future__ import annotations

import argparse
import logging
import uuid

from sqlalchemy import func

from backend.app.database import SessionLocal
from backend.app.models import (
    Document,
    ExtractionRun,
    ExtractionStage,
    Obligation,
    Risk,
)
from backend.app.worker.tasks.critic import criticize_extractions
from backend.app.worker.tasks.notify import emit_notifications, persist_final_status
from backend.app.worker.tasks.rescore import rescore_with_llm
from backend.app.worker.tasks.score import score_extractions
from backend.app.worker.tasks.verify import verify_extractions

logger = logging.getLogger(__name__)


def _latest_run_id(db, document_id: uuid.UUID, stage: ExtractionStage) -> uuid.UUID | None:
    row = (
        db.query(ExtractionRun.id)
        .filter(ExtractionRun.document_id == document_id, ExtractionRun.stage == stage)
        .order_by(ExtractionRun.started_at.desc())
        .first()
    )
    return row[0] if row else None


def _dedup_to_latest_run(db, document_id: uuid.UUID, *, dry_run: bool) -> dict[str, object]:
    """Delete obligations/risks not linked to the most recent extraction_run per stage.

    Safe to run only before downstream stages (no reviews/contradictions exist yet).
    """
    latest_ob_run = _latest_run_id(db, document_id, ExtractionStage.obligation_extraction)
    latest_ri_run = _latest_run_id(db, document_id, ExtractionStage.risk_extraction)

    stats: dict[str, object] = {
        "latest_obligation_run": str(latest_ob_run) if latest_ob_run else None,
        "latest_risk_run": str(latest_ri_run) if latest_ri_run else None,
        "obligations_before": db.query(func.count(Obligation.id)).filter(Obligation.document_id == document_id).scalar(),
        "risks_before": db.query(func.count(Risk.id)).filter(Risk.document_id == document_id).scalar(),
        "obligations_deleted": 0,
        "risks_deleted": 0,
    }

    if latest_ob_run is not None:
        q = db.query(Obligation).filter(
            Obligation.document_id == document_id,
            Obligation.extraction_run_id != latest_ob_run,
        )
        stats["obligations_deleted"] = q.count()
        if not dry_run:
            q.delete(synchronize_session=False)

    if latest_ri_run is not None:
        q = db.query(Risk).filter(
            Risk.document_id == document_id,
            Risk.extraction_run_id != latest_ri_run,
        )
        stats["risks_deleted"] = q.count()
        if not dry_run:
            q.delete(synchronize_session=False)

    if not dry_run:
        db.commit()

    stats["obligations_after"] = db.query(func.count(Obligation.id)).filter(Obligation.document_id == document_id).scalar()
    stats["risks_after"] = db.query(func.count(Risk.id)).filter(Risk.document_id == document_id).scalar()
    return stats


_STAGES = [
    ("9-verify", verify_extractions),
    ("9a-critic", criticize_extractions),
    ("10-score", score_extractions),
    ("10b-rescore", rescore_with_llm),
    ("11-persist", persist_final_status),
    ("12-notify", emit_notifications),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--keep-all", action="store_true", help="Skip dedup; run downstream on ALL existing items")
    parser.add_argument("--dry-run", action="store_true", help="Print dedup plan, do not modify DB or run stages")
    parser.add_argument("--from-stage", default=None, help="Skip earlier stages, e.g. '10-score'")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        doc_id = uuid.UUID(args.document_id)
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            print(f"Document {doc_id} not found")
            return
        print(f"Document: {doc.source_name}  parse_status={doc.parse_status}  doc_type={doc.doc_type}")

        if not args.keep_all:
            stats = _dedup_to_latest_run(db, doc_id, dry_run=args.dry_run)
            print("=== Dedup ===")
            for k, v in stats.items():
                print(f"  {k}: {v}")

        if args.dry_run:
            print("Dry run — exiting before downstream stages.")
            return
    finally:
        db.close()

    started = False if args.from_stage else True
    for step_id, fn in _STAGES:
        if not started:
            if step_id == args.from_stage:
                started = True
            else:
                print(f"--- skipping {step_id}")
                continue
        print(f"--- running {step_id} ({fn.__name__}) ---")
        try:
            result = fn(args.document_id)
            print(f"    result: {result}")
        except Exception as exc:
            print(f"    FAILED: {exc!r}")
            raise

    print("Done.")


if __name__ == "__main__":
    main()
