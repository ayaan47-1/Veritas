"""Evaluate pipeline extraction quality against AI-generated ground truth.

Usage:
    python3 -m backend.tools.evaluate_pipeline --document-id <uuid>
    python3 -m backend.tools.evaluate_pipeline --document-id <uuid> --output json
    python3 -m backend.tools.evaluate_pipeline --document-id <uuid> --threshold 0.5

Metrics reported:
  Precision  — what fraction of pipeline extractions match a ground truth item
  Recall     — what fraction of ground truth items were found by the pipeline
  F1         — harmonic mean of precision and recall
  Severity exact match rate    — matched pairs with identical severity tier
  Severity adjacent agreement  — matched pairs within one tier of each other
  Spearman rank correlation     — on severity tiers of matched pairs

Match algorithm: ROUGE-L (longest common subsequence F1) on lowercased word tokens.
Items match when ROUGE-L F1 >= threshold (default 0.5).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# Allow running as `python3 -m backend.tools.evaluate_pipeline`
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.database import SessionLocal
from backend.app.models import Obligation, Risk

_SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# ── helpers ──────────────────────────────────────────────────────────────────

def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence of two token lists."""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    # Space-optimised DP: two rows
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n]


def _rouge_l(a: list[str], b: list[str]) -> float:
    """ROUGE-L F1 score between two token lists."""
    if not a or not b:
        return 0.0
    lcs = _lcs_length(a, b)
    precision = lcs / len(b)
    recall = lcs / len(a)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Compute Spearman rank correlation without scipy."""
    n = len(xs)
    if n < 2:
        return None

    def _ranks(vals: list[float]) -> list[float]:
        sorted_vals = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * n
        for rank, (orig_idx, _) in enumerate(sorted_vals, start=1):
            ranks[orig_idx] = float(rank)
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    d_sq = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1.0 - (6.0 * d_sq) / (n * (n * n - 1))


# ── matching ─────────────────────────────────────────────────────────────────

def _match_items(
    gt_items: list[dict],
    pipeline_items: list[dict],
    gt_quote_key: str,
    pl_quote_key: str,
    threshold: float,
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Return (matched_pairs, false_negatives, false_positives)."""
    gt_tokens = [_word_tokens(item.get(gt_quote_key, "")) for item in gt_items]
    pl_tokens = [_word_tokens(item.get(pl_quote_key, "")) for item in pipeline_items]

    matched_pl: set[int] = set()
    matched_gt: set[int] = set()
    pairs: list[tuple[dict, dict]] = []

    for gi, gt in enumerate(gt_items):
        best_score = threshold - 1e-9
        best_pi = -1
        for pi in range(len(pipeline_items)):
            if pi in matched_pl:
                continue
            score = _rouge_l(gt_tokens[gi], pl_tokens[pi])
            if score > best_score:
                best_score = score
                best_pi = pi
        if best_pi >= 0:
            pairs.append((gt, pipeline_items[best_pi]))
            matched_pl.add(best_pi)
            matched_gt.add(gi)

    false_negatives = [gt_items[i] for i in range(len(gt_items)) if i not in matched_gt]
    false_positives = [pipeline_items[i] for i in range(len(pipeline_items)) if i not in matched_pl]
    return pairs, false_negatives, false_positives


# ── metrics ──────────────────────────────────────────────────────────────────

def _severity_metrics(pairs: list[tuple[dict, dict]], gt_key: str, pl_key: str) -> dict[str, Any]:
    exact = 0
    adjacent = 0
    gt_ranks: list[float] = []
    pl_ranks: list[float] = []

    for gt, pl in pairs:
        gs = gt.get(gt_key, "")
        ps = pl.get(pl_key, "")
        gr = _SEVERITY_RANK.get(gs, 0)
        pr = _SEVERITY_RANK.get(ps, 0)
        if gr and pr:
            gt_ranks.append(float(gr))
            pl_ranks.append(float(pr))
            if gs == ps:
                exact += 1
            if abs(gr - pr) <= 1:
                adjacent += 1

    n = len(pairs)
    return {
        "exact_match_rate": round(exact / n, 3) if n else None,
        "adjacent_agreement_rate": round(adjacent / n, 3) if n else None,
        "spearman_rho": round(_spearman(gt_ranks, pl_ranks) or 0.0, 3) if len(gt_ranks) >= 2 else None,
    }


def _compute(
    gt_items: list[dict],
    pipeline_items: list[dict],
    gt_quote_key: str,
    pl_quote_key: str,
    gt_severity_key: str,
    pl_severity_key: str,
    threshold: float,
) -> dict[str, Any]:
    pairs, fn, fp = _match_items(gt_items, pipeline_items, gt_quote_key, pl_quote_key, threshold)
    tp = len(pairs)
    precision = tp / (tp + len(fp)) if (tp + len(fp)) > 0 else None
    recall = tp / (tp + len(fn)) if (tp + len(fn)) > 0 else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    sev = _severity_metrics(pairs, gt_severity_key, pl_severity_key)
    return {
        "ground_truth_count": len(gt_items),
        "pipeline_count": len(pipeline_items),
        "true_positives": tp,
        "false_negatives": len(fn),
        "false_positives": len(fp),
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "f1": round(f1, 3) if f1 is not None else None,
        **sev,
        "missed": [
            {"severity": item.get(gt_severity_key), "quote": (item.get(gt_quote_key) or "")[:120]}
            for item in fn
        ],
        "extra": [
            {"severity": item.get(pl_severity_key), "quote": (item.get(pl_quote_key) or "")[:120]}
            for item in fp
        ],
    }


# ── pipeline query ────────────────────────────────────────────────────────────

def _pipeline_obligations(db, doc_id: uuid.UUID) -> list[dict]:
    rows = db.query(Obligation).filter(Obligation.document_id == doc_id).all()
    return [
        {
            "quote": row.obligation_text or "",
            "obligation_type": row.obligation_type.value if row.obligation_type else "",
            "severity": row.severity.value if row.severity else "",
            "system_confidence": row.system_confidence,
            "status": row.status.value if row.status else "",
        }
        for row in rows
    ]


def _pipeline_risks(db, doc_id: uuid.UUID) -> list[dict]:
    rows = db.query(Risk).filter(Risk.document_id == doc_id).all()
    return [
        {
            "quote": row.risk_text or "",
            "risk_type": row.risk_type.value if row.risk_type else "",
            "severity": row.severity.value if row.severity else "",
            "system_confidence": row.system_confidence,
            "status": row.status.value if row.status else "",
        }
        for row in rows
        if not row.contradiction_flag  # exclude auto-generated contradiction risks
    ]


# ── report rendering ──────────────────────────────────────────────────────────

def _pct(val: float | None) -> str:
    return f"{val * 100:.1f}%" if val is not None else "N/A"


def _render_text(doc_id: str, gt_meta: dict, ob: dict, ri: dict) -> str:
    lines = [
        "=" * 60,
        f"EVALUATION REPORT — {doc_id[:8]}",
        f"Ground truth model : {gt_meta.get('model', '?')}",
        f"Generated at       : {gt_meta.get('generated_at', '?')}",
        "=" * 60,
        "",
        "OBLIGATIONS",
        f"  GT count          : {ob['ground_truth_count']}",
        f"  Pipeline count    : {ob['pipeline_count']}",
        f"  True positives    : {ob['true_positives']}",
        f"  False negatives   : {ob['false_negatives']}  (missed by pipeline)",
        f"  False positives   : {ob['false_positives']}  (extracted but not in GT)",
        f"  Precision         : {_pct(ob['precision'])}",
        f"  Recall            : {_pct(ob['recall'])}",
        f"  F1                : {_pct(ob['f1'])}",
        f"  Severity exact    : {_pct(ob['exact_match_rate'])}",
        f"  Severity adjacent : {_pct(ob['adjacent_agreement_rate'])}",
        f"  Spearman ρ        : {ob['spearman_rho'] if ob['spearman_rho'] is not None else 'N/A'}",
        "",
        "RISKS",
        f"  GT count          : {ri['ground_truth_count']}",
        f"  Pipeline count    : {ri['pipeline_count']}",
        f"  True positives    : {ri['true_positives']}",
        f"  False negatives   : {ri['false_negatives']}  (missed by pipeline)",
        f"  False positives   : {ri['false_positives']}  (extracted but not in GT)",
        f"  Precision         : {_pct(ri['precision'])}",
        f"  Recall            : {_pct(ri['recall'])}",
        f"  F1                : {_pct(ri['f1'])}",
        f"  Severity exact    : {_pct(ri['exact_match_rate'])}",
        f"  Severity adjacent : {_pct(ri['adjacent_agreement_rate'])}",
        f"  Spearman ρ        : {ri['spearman_rho'] if ri['spearman_rho'] is not None else 'N/A'}",
    ]

    if ob["missed"]:
        lines += ["", "MISSED OBLIGATIONS (false negatives):"]
        for item in ob["missed"]:
            lines.append(f"  [{item['severity']:8}] {item['quote']}")

    if ri["missed"]:
        lines += ["", "MISSED RISKS (false negatives):"]
        for item in ri["missed"]:
            lines.append(f"  [{item['severity']:8}] {item['quote']}")

    if ob["extra"]:
        lines += ["", "EXTRA OBLIGATIONS (false positives):"]
        for item in ob["extra"]:
            lines.append(f"  [{item['severity']:8}] {item['quote']}")

    if ri["extra"]:
        lines += ["", "EXTRA RISKS (false positives):"]
        for item in ri["extra"]:
            lines.append(f"  [{item['severity']:8}] {item['quote']}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ── entrypoint ────────────────────────────────────────────────────────────────

def evaluate(
    document_id: str,
    gt_dir: Path,
    threshold: float = 0.5,
    output_format: str = "text",
) -> dict[str, Any]:
    doc_id = uuid.UUID(document_id)
    gt_path = gt_dir / str(doc_id) / "ground_truth.json"
    if not gt_path.exists():
        raise FileNotFoundError(
            f"Ground truth not found at {gt_path}. "
            "Run generate_ground_truth.py first."
        )

    gt = json.loads(gt_path.read_text())

    db = SessionLocal()
    try:
        pl_obligations = _pipeline_obligations(db, doc_id)
        pl_risks = _pipeline_risks(db, doc_id)
    finally:
        db.close()

    ob_metrics = _compute(
        gt_items=gt.get("obligations", []),
        pipeline_items=pl_obligations,
        gt_quote_key="quote",
        pl_quote_key="quote",
        gt_severity_key="severity",
        pl_severity_key="severity",
        threshold=threshold,
    )
    ri_metrics = _compute(
        gt_items=gt.get("risks", []),
        pipeline_items=pl_risks,
        gt_quote_key="quote",
        pl_quote_key="quote",
        gt_severity_key="severity",
        pl_severity_key="severity",
        threshold=threshold,
    )

    result = {
        "document_id": str(doc_id),
        "threshold": threshold,
        "obligations": ob_metrics,
        "risks": ri_metrics,
    }

    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(_render_text(str(doc_id), gt, ob_metrics, ri_metrics))

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate pipeline extraction quality vs AI ground truth")
    parser.add_argument("--document-id", required=True, help="UUID of the processed document")
    parser.add_argument(
        "--gt-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "benchmarks"),
        help="Directory containing ground truth JSON files",
    )
    parser.add_argument("--threshold", type=float, default=0.5, help="ROUGE-L F1 match threshold (default 0.5)")
    parser.add_argument("--output", choices=["text", "json"], default="text", help="Output format")
    args = parser.parse_args()

    evaluate(
        document_id=args.document_id,
        gt_dir=Path(args.gt_dir),
        threshold=args.threshold,
        output_format=args.output,
    )


if __name__ == "__main__":
    main()
