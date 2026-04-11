"""Experiment: full-document extraction using pipeline prompts.

Sends the entire document text in a single LLM call per stage (obligations, risks),
then runs the matching against existing ground truth. This tests whether the chunk-based
architecture is the F1 bottleneck.

Usage:
    python3 -m backend.tools.experiment_fulldoc --document-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.database import SessionLocal
from backend.app.models import Chunk, Document
from backend.app.services.llm import llm_completion, parse_json_list
from backend.app.worker.tasks.extract import _OBLIGATION_SCHEMA, _RISK_SCHEMA
from backend.tools.evaluate_pipeline import _match_items, _severity_metrics, _pct

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fetch_full_text(db, doc_id: uuid.UUID) -> tuple[str, int, int]:
    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == doc_id)
        .order_by(Chunk.page_number, Chunk.char_start)
        .all()
    )
    if not chunks:
        raise ValueError(f"No chunks found for document {doc_id}")
    pages = [c.page_number for c in chunks if c.page_number is not None]
    full_text = "\n\n".join(c.text or "" for c in chunks)
    return full_text, min(pages, default=1), max(pages, default=1)


def _build_fulldoc_prompt(schema: str, doc_type: str, full_text: str, first_page: int, last_page: int) -> str:
    return (
        f"Document type: {doc_type}\n"
        f"Pages: {first_page}\u2013{last_page}\n\n"
        f"{schema}\n\n"
        f"Full document text:\n{full_text}"
    )


def _run_matching(gt_items, extracted_items, quote_key, severity_key, threshold):
    pairs, fn, fp = _match_items(gt_items, extracted_items, quote_key, quote_key, threshold)
    tp = len(pairs)
    precision = tp / (tp + len(fp)) if (tp + len(fp)) > 0 else 0
    recall = tp / (tp + len(fn)) if (tp + len(fn)) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    sev = _severity_metrics(pairs, severity_key, severity_key)
    return {
        "gt_count": len(gt_items),
        "extracted_count": len(extracted_items),
        "true_positives": tp,
        "false_negatives": len(fn),
        "false_positives": len(fp),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        **sev,
    }


def experiment(document_id: str, gt_dir: Path, model: str, threshold: float) -> None:
    doc_id = uuid.UUID(document_id)

    gt_path = gt_dir / str(doc_id) / "ground_truth.json"
    if not gt_path.exists():
        raise FileNotFoundError(f"Ground truth not found at {gt_path}. Run generate_ground_truth.py first.")
    gt = json.loads(gt_path.read_text())

    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            raise ValueError(f"Document {doc_id} not found")
        full_text, first_page, last_page = _fetch_full_text(db, doc_id)
        doc_type = document.doc_type.value
    finally:
        db.close()

    logger.info("Full text: %d chars, pages %d\u2013%d", len(full_text), first_page, last_page)

    # --- Obligations ---
    logger.info("Extracting obligations (full-doc, single call)...")
    ob_prompt = _build_fulldoc_prompt(_OBLIGATION_SCHEMA, doc_type, full_text, first_page, last_page)
    ob_raw = llm_completion(model, ob_prompt)
    ob_items = parse_json_list(ob_raw)
    logger.info("Extracted %d obligations", len(ob_items))

    # --- Risks ---
    logger.info("Extracting risks (full-doc, single call)...")
    ri_prompt = _build_fulldoc_prompt(_RISK_SCHEMA, doc_type, full_text, first_page, last_page)
    ri_raw = llm_completion(model, ri_prompt)
    ri_items = parse_json_list(ri_raw)
    logger.info("Extracted %d risks", len(ri_items))

    # --- Matching against GT ---
    ob_metrics = _run_matching(gt.get("obligations", []), ob_items, "quote", "severity", threshold)
    ri_metrics = _run_matching(gt.get("risks", []), ri_items, "quote", "severity", threshold)

    print()
    print("=" * 60)
    print(f"FULL-DOC EXPERIMENT \u2014 {document_id[:8]}")
    print(f"Model: {model}")
    print("=" * 60)
    print()
    print("OBLIGATIONS")
    print(f"  GT count          : {ob_metrics['gt_count']}")
    print(f"  Extracted count   : {ob_metrics['extracted_count']}")
    print(f"  True positives    : {ob_metrics['true_positives']}")
    print(f"  False negatives   : {ob_metrics['false_negatives']}")
    print(f"  False positives   : {ob_metrics['false_positives']}")
    print(f"  Precision         : {_pct(ob_metrics['precision'])}")
    print(f"  Recall            : {_pct(ob_metrics['recall'])}")
    print(f"  F1                : {_pct(ob_metrics['f1'])}")
    print(f"  Severity exact    : {_pct(ob_metrics.get('exact_match_rate'))}")
    print(f"  Severity adjacent : {_pct(ob_metrics.get('adjacent_agreement_rate'))}")
    print()
    print("RISKS")
    print(f"  GT count          : {ri_metrics['gt_count']}")
    print(f"  Extracted count   : {ri_metrics['extracted_count']}")
    print(f"  True positives    : {ri_metrics['true_positives']}")
    print(f"  False negatives   : {ri_metrics['false_negatives']}")
    print(f"  False positives   : {ri_metrics['false_positives']}")
    print(f"  Precision         : {_pct(ri_metrics['precision'])}")
    print(f"  Recall            : {_pct(ri_metrics['recall'])}")
    print(f"  F1                : {_pct(ri_metrics['f1'])}")
    print(f"  Severity exact    : {_pct(ri_metrics.get('exact_match_rate'))}")
    print(f"  Severity adjacent : {_pct(ri_metrics.get('adjacent_agreement_rate'))}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Full-document extraction experiment")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument(
        "--gt-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "benchmarks"),
    )
    args = parser.parse_args()
    experiment(args.document_id, Path(args.gt_dir), args.model, args.threshold)


if __name__ == "__main__":
    main()
