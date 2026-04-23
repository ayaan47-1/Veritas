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
            .order_by(ExtractionRun.started_at.desc())
            .limit(12)
            .all()
        )
        for r in runs:
            raw = r.raw_llm_output or {}
            cfg = r.config_snapshot or {}
            interesting = {
                k: v
                for k, v in {**(cfg if isinstance(cfg, dict) else {}), **(raw if isinstance(raw, dict) else {})}.items()
                if any(token in k for token in ("section_filter", "zero_result", "chunk_source", "bypassed", "counts"))
            }
            meta_str = json.dumps(interesting, default=str)[:400] if interesting else "{}"
            err = (r.error or "")[:200]
            print(f"{r.stage} | {r.status} | started={r.started_at} | meta={meta_str} | err={err}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
