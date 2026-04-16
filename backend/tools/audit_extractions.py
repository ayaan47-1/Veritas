"""Audit pipeline false positives with an LLM judge.

The `evaluate_pipeline` tool reports precision/recall against an AI-generated
ground truth, but the ground truth is selective (it doesn't exhaustively label
every minor clause). Many "false positives" are actually legitimate contractual
items that the GT missed.

This tool takes the false positives from the pipeline vs GT comparison and
asks an LLM judge: "is this a legitimate contractual obligation/risk from the
agreement body, or is it boilerplate/statutory/spurious?" It then computes
an **adjusted precision** treating judge-accepted items as true positives.

Usage:
    python3 -m backend.tools.audit_extractions --document-id <uuid>
    python3 -m backend.tools.audit_extractions --document-id <uuid> --model claude-sonnet-4-6

Output:
    - Per-item judgment (accept/reject) with short reason
    - Adjusted precision / F1
    - Breakdown of rejection categories
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

# Allow running as `python3 -m backend.tools.audit_extractions`
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.database import SessionLocal
from backend.app.models import Document
from backend.app.services.llm import llm_completion, parse_json_list
from backend.tools.evaluate_pipeline import (
    _match_items,
    _pipeline_obligations,
    _pipeline_risks,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


_JUDGE_PROMPT = """You are an expert contract analyst acting as a judge.

Your job: for each candidate below, decide whether it is a LEGITIMATE CONTRACTUAL obligation/risk from the agreement body, or a false positive.

ACCEPT as legitimate if it is:
- A commitment between the parties of THIS specific agreement (e.g., "Lessee shall...", "Lessor shall...", "Tenant agrees to...")
- A conditional obligation arising from the agreement ("If X occurs, Tenant shall...")
- A penalty, default, or remedy clause tied to breach of THIS agreement
- A specific payment, notice, maintenance, or access term unique to this contract

REJECT as false positive if it is:
- A restatement of statutory language ("A landlord must...", "Under [Code] §...", "Illinois law requires...")
- Content from an appended statutory disclosure or tenant-rights section
- Boilerplate definitions, recitals, or preamble without a duty
- A bare factual statement with no obligation/risk
- A general acknowledgment or "informational only" text

Return ONLY a JSON array with one object per candidate, in the same order:
[
  {{"index": 0, "verdict": "accept" or "reject", "reason": "<10-20 word explanation>"}},
  ...
]

Document type: {doc_type}
Category being judged: {category}

CANDIDATES:
{items}"""


def _format_candidates(items: list[dict], quote_key: str) -> str:
    """Format candidates as numbered list."""
    lines = []
    for idx, item in enumerate(items):
        quote = (item.get(quote_key) or "").strip()
        lines.append(f"[{idx}] {quote}")
    return "\n\n".join(lines)


def _judge_batch(
    items: list[dict],
    quote_key: str,
    category: str,
    doc_type: str,
    model: str,
) -> list[dict]:
    """Send a batch of candidates to the LLM judge. Returns list of verdicts."""
    if not items:
        return []
    prompt = _JUDGE_PROMPT.format(
        doc_type=doc_type,
        category=category,
        items=_format_candidates(items, quote_key),
    )
    raw = llm_completion(model, prompt, prefer_json_object=False)
    verdicts = parse_json_list(raw)
    by_index: dict[int, dict] = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        idx = v.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            by_index[idx] = v
    # Default to reject if the judge forgot an item
    return [
        by_index.get(
            i, {"index": i, "verdict": "reject", "reason": "missing judgment"}
        )
        for i in range(len(items))
    ]


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _audit_category(
    false_positives: list[dict],
    quote_key: str,
    category: str,
    doc_type: str,
    model: str,
    batch_size: int,
) -> list[dict]:
    """Run the judge on all false positives, batched. Returns list of enriched dicts."""
    if not false_positives:
        return []

    results: list[dict] = []
    batches = _chunked(false_positives, batch_size)
    for bi, batch in enumerate(batches):
        logger.info(
            "Judging %s batch %d/%d (%d items)", category, bi + 1, len(batches), len(batch)
        )
        verdicts = _judge_batch(batch, quote_key, category, doc_type, model)
        for item, verdict in zip(batch, verdicts):
            enriched = {
                **item,
                "verdict": verdict.get("verdict", "reject"),
                "reason": verdict.get("reason", ""),
            }
            results.append(enriched)
    return results


def _adjusted_metrics(
    tp: int,
    accepted_fp: int,
    rejected_fp: int,
    fn: int,
) -> dict[str, float]:
    """Compute adjusted precision/recall/F1 treating accepted FPs as TPs."""
    new_tp = tp + accepted_fp
    precision = new_tp / (new_tp + rejected_fp) if (new_tp + rejected_fp) > 0 else 0.0
    recall = new_tp / (new_tp + fn) if (new_tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def audit(
    document_id: str,
    gt_dir: Path,
    model: str,
    threshold: float,
    batch_size: int,
) -> dict[str, Any]:
    doc_id = uuid.UUID(document_id)
    gt_path = gt_dir / str(doc_id) / "ground_truth.json"
    if not gt_path.exists():
        raise FileNotFoundError(f"Ground truth not found at {gt_path}")

    gt = json.loads(gt_path.read_text())

    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            raise ValueError(f"Document {doc_id} not found")
        doc_type = document.doc_type.value
        pl_obs = _pipeline_obligations(db, doc_id)
        pl_ris = _pipeline_risks(db, doc_id)
    finally:
        db.close()

    # Match pipeline items to GT to find false positives
    ob_pairs, ob_fn, ob_fp = _match_items(
        gt.get("obligations", []), pl_obs, "quote", "quote", threshold
    )
    ri_pairs, ri_fn, ri_fp = _match_items(
        gt.get("risks", []), pl_ris, "quote", "quote", threshold
    )

    logger.info(
        "Found %d obligation FPs and %d risk FPs to audit",
        len(ob_fp), len(ri_fp),
    )

    # Run judge on false positives
    ob_judged = _audit_category(ob_fp, "quote", "obligations", doc_type, model, batch_size)
    ri_judged = _audit_category(ri_fp, "quote", "risks", doc_type, model, batch_size)

    ob_accepted = [x for x in ob_judged if x.get("verdict") == "accept"]
    ob_rejected = [x for x in ob_judged if x.get("verdict") != "accept"]
    ri_accepted = [x for x in ri_judged if x.get("verdict") == "accept"]
    ri_rejected = [x for x in ri_judged if x.get("verdict") != "accept"]

    ob_adj = _adjusted_metrics(
        tp=len(ob_pairs),
        accepted_fp=len(ob_accepted),
        rejected_fp=len(ob_rejected),
        fn=len(ob_fn),
    )
    ri_adj = _adjusted_metrics(
        tp=len(ri_pairs),
        accepted_fp=len(ri_accepted),
        rejected_fp=len(ri_rejected),
        fn=len(ri_fn),
    )

    # ── Print report ────────────────────────────────────────────
    print("=" * 70)
    print(f"AUDIT REPORT — {doc_id}")
    print(f"Judge model: {model}")
    print("=" * 70)
    print()
    print("OBLIGATIONS")
    print(f"  GT count            : {len(gt.get('obligations', []))}")
    print(f"  Pipeline count      : {len(pl_obs)}")
    print(f"  True positives      : {len(ob_pairs)}")
    print(f"  False positives     : {len(ob_fp)}")
    print(f"    → judge accepted  : {len(ob_accepted)}  (legitimate, GT missed)")
    print(f"    → judge rejected  : {len(ob_rejected)}  (real false positives)")
    print(f"  False negatives     : {len(ob_fn)}")
    print()
    print(f"  ORIGINAL  precision : {_pct(len(ob_pairs) / len(pl_obs)) if pl_obs else 'N/A'}")
    print(f"  ADJUSTED  precision : {_pct(ob_adj['precision'])}")
    print(f"  ADJUSTED  recall    : {_pct(ob_adj['recall'])}")
    print(f"  ADJUSTED  F1        : {_pct(ob_adj['f1'])}")
    print()
    print("RISKS")
    print(f"  GT count            : {len(gt.get('risks', []))}")
    print(f"  Pipeline count      : {len(pl_ris)}")
    print(f"  True positives      : {len(ri_pairs)}")
    print(f"  False positives     : {len(ri_fp)}")
    print(f"    → judge accepted  : {len(ri_accepted)}  (legitimate, GT missed)")
    print(f"    → judge rejected  : {len(ri_rejected)}  (real false positives)")
    print(f"  False negatives     : {len(ri_fn)}")
    print()
    print(f"  ORIGINAL  precision : {_pct(len(ri_pairs) / len(pl_ris)) if pl_ris else 'N/A'}")
    print(f"  ADJUSTED  precision : {_pct(ri_adj['precision'])}")
    print(f"  ADJUSTED  recall    : {_pct(ri_adj['recall'])}")
    print(f"  ADJUSTED  F1        : {_pct(ri_adj['f1'])}")
    print("=" * 70)
    print()

    if ob_rejected:
        print("REAL FALSE POSITIVE OBLIGATIONS (judge rejected):")
        for item in ob_rejected[:20]:
            quote = (item.get("quote") or "")[:100]
            reason = item.get("reason", "")
            print(f"  - {quote}")
            print(f"      reason: {reason}")
        if len(ob_rejected) > 20:
            print(f"  ... and {len(ob_rejected) - 20} more")
        print()

    if ri_rejected:
        print("REAL FALSE POSITIVE RISKS (judge rejected):")
        for item in ri_rejected[:20]:
            quote = (item.get("quote") or "")[:100]
            reason = item.get("reason", "")
            print(f"  - {quote}")
            print(f"      reason: {reason}")
        if len(ri_rejected) > 20:
            print(f"  ... and {len(ri_rejected) - 20} more")
        print()

    return {
        "document_id": document_id,
        "model": model,
        "obligations": {
            "tp": len(ob_pairs),
            "fp_total": len(ob_fp),
            "fp_accepted": len(ob_accepted),
            "fp_rejected": len(ob_rejected),
            "fn": len(ob_fn),
            "adjusted": ob_adj,
        },
        "risks": {
            "tp": len(ri_pairs),
            "fp_total": len(ri_fp),
            "fp_accepted": len(ri_accepted),
            "fp_rejected": len(ri_rejected),
            "fn": len(ri_fn),
            "adjusted": ri_adj,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit pipeline false positives with LLM-as-judge")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Judge model")
    parser.add_argument("--threshold", type=float, default=0.5, help="ROUGE-L match threshold")
    parser.add_argument("--batch-size", type=int, default=15, help="Candidates per LLM call")
    parser.add_argument(
        "--gt-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "benchmarks"),
    )
    args = parser.parse_args()
    audit(
        document_id=args.document_id,
        gt_dir=Path(args.gt_dir),
        model=args.model,
        threshold=args.threshold,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
