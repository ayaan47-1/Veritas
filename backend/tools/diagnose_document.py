"""Dump document state, pages, chunks, extraction counts, and run metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func

from backend.app.database import SessionLocal
from backend.app.models import (
    Chunk,
    Document,
    DocumentPage,
    ExtractionRun,
    Obligation,
    Risk,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    args = parser.parse_args()
    doc_id = args.document_id

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        print("=== Document ===")
        if doc is None:
            print(f"  NOT FOUND: {doc_id}")
            return
        for attr in ("id", "source_name", "sha256", "parse_status", "doc_type", "total_pages", "scanned_page_count", "uploaded_at", "uploaded_by", "file_path", "processed_file_path", "notes"):
            print(f"  {attr}: {getattr(doc, attr, '<missing>')}")

        file_path = getattr(doc, "file_path", None)
        if file_path:
            p = Path(file_path)
            print(f"  file_exists: {p.exists()}  size: {p.stat().st_size if p.exists() else 'N/A'}")

        print()
        pages = db.query(func.count(DocumentPage.id)).filter(DocumentPage.document_id == doc_id).scalar()
        print(f"=== DocumentPages: {pages} ===")
        if pages:
            avg_raw = db.query(func.avg(func.length(DocumentPage.raw_text))).filter(DocumentPage.document_id == doc_id).scalar()
            avg_norm = db.query(func.avg(func.length(DocumentPage.normalized_text))).filter(DocumentPage.document_id == doc_id).scalar()
            by_status = (
                db.query(DocumentPage.processing_status, func.count(DocumentPage.id))
                .filter(DocumentPage.document_id == doc_id)
                .group_by(DocumentPage.processing_status)
                .all()
            )
            by_source = (
                db.query(DocumentPage.text_source, func.count(DocumentPage.id))
                .filter(DocumentPage.document_id == doc_id)
                .group_by(DocumentPage.text_source)
                .all()
            )
            print(f"  avg_raw_len: {avg_raw}  avg_normalized_len: {avg_norm}")
            for status, count in by_status:
                print(f"  processing_status {status!r}: {count}")
            for source, count in by_source:
                print(f"  text_source {source!r}: {count}")

        print()
        chunks_total = db.query(func.count(Chunk.id)).filter(Chunk.document_id == doc_id).scalar()
        avg_len = db.query(func.avg(func.length(Chunk.text))).filter(Chunk.document_id == doc_id).scalar()
        labels = (
            db.query(Chunk.section_label, func.count(Chunk.id))
            .filter(Chunk.document_id == doc_id)
            .group_by(Chunk.section_label)
            .all()
        )
        print(f"=== Chunks: {chunks_total}  avg_len: {avg_len} ===")
        for label, count in labels:
            print(f"  {label!r}: {count}")

        print()
        obl = db.query(func.count(Obligation.id)).filter(Obligation.document_id == doc_id).scalar()
        rsk = db.query(func.count(Risk.id)).filter(Risk.document_id == doc_id).scalar()
        print(f"=== Obligations: {obl}  Risks: {rsk} ===")

        print()
        runs = (
            db.query(ExtractionRun)
            .filter(ExtractionRun.document_id == doc_id)
            .order_by(ExtractionRun.started_at.desc())
            .limit(12)
            .all()
        )
        print(f"=== Recent ExtractionRuns ({len(runs)}) ===")
        for r in runs:
            raw = r.raw_llm_output or {}
            cfg = r.config_snapshot or {}
            interesting = {
                k: v
                for k, v in {**(cfg if isinstance(cfg, dict) else {}), **(raw if isinstance(raw, dict) else {})}.items()
                if any(token in k for token in ("section_filter", "zero_result", "chunk_source", "bypassed", "counts"))
            }
            meta_str = json.dumps(interesting, default=str)[:300] if interesting else "{}"
            err = (r.error or "")[:200]
            print(f"  {r.stage} | {r.status} | started={r.started_at} | meta={meta_str} | err={err}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
