"""Dump chunk labels, extraction counts, and run metadata for one document."""
from __future__ import annotations

import argparse
import json

from sqlalchemy import func

from backend.app.database import SessionLocal
from backend.app.models import Chunk, ExtractionRun, Obligation, Risk


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    args = parser.parse_args()
    doc = args.document_id

    db = SessionLocal()
    try:
        total = db.query(func.count(Chunk.id)).filter(Chunk.document_id == doc).scalar()
        avg_len = db.query(func.avg(func.length(Chunk.text))).filter(Chunk.document_id == doc).scalar()
        labels = (
            db.query(Chunk.section_label, func.count(Chunk.id))
            .filter(Chunk.document_id == doc)
            .group_by(Chunk.section_label)
            .all()
        )
        print(f"Chunks: {total}, avg_len: {avg_len}")
        for label, count in labels:
            print(f"  {label!r}: {count}")

        print()
        obl = db.query(func.count(Obligation.id)).filter(Obligation.document_id == doc).scalar()
        rsk = db.query(func.count(Risk.id)).filter(Risk.document_id == doc).scalar()
        print(f"Obligations: {obl}  Risks: {rsk}")

        print()
        runs = (
            db.query(ExtractionRun)
            .filter(ExtractionRun.document_id == doc)
            .order_by(ExtractionRun.created_at.desc())
            .limit(12)
            .all()
        )
        for r in runs:
            meta = getattr(r, "run_metadata", None) or getattr(r, "metadata_json", None) or {}
            if hasattr(meta, "copy"):
                meta_str = json.dumps(meta, default=str)[:300]
            else:
                meta_str = str(meta)[:300]
            print(f"{r.stage} | {r.status} | meta={meta_str}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
