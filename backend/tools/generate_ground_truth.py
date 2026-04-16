"""Generate AI ground truth labels for a processed document.

Usage:
    python3 -m backend.tools.generate_ground_truth --document-id <uuid>
    python3 -m backend.tools.generate_ground_truth --document-id <uuid> --output-dir /path/to/benchmarks

The script reads ALL chunks for the document (no MMR limit), sends the full text to
Claude Sonnet, and asks it to exhaustively label every obligation and risk with
verbatim quotes, type, severity, and reasoning. Output is saved as JSON.

Severity definitions used in the prompt:
  critical — financial penalty/termination/indemnification/liability exposure
  high     — mandatory compliance deadline or statutory requirement
  medium   — standard contractual duty (shall/must without direct penalty)
  low      — procedural or administrative obligation
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python3 -m backend.tools.generate_ground_truth`
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.database import SessionLocal
from backend.app.models import Chunk, Document
from backend.app.services.llm import llm_completion, parse_json_dict

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_GT_PROMPT = """You are an expert construction contract analyst. Your task is to exhaustively identify every obligation and risk in the contract text below.

OBLIGATION SEVERITY DEFINITIONS (use these exactly for obligations):
- critical: financial penalty clause, liquidated damages, indemnification, termination rights, bond/insurance requirements with termination consequences
- high:     mandatory compliance with statute/regulation, hard deadlines with consequences, OSHA/safety requirements
- medium:   standard contractual duty (shall/must) without direct penalty language, payment terms, submission requirements
- low:      procedural or administrative duties, notice requirements, record-keeping, formatting requirements

RISK SEVERITY DEFINITIONS (use these exactly for risks):
- critical: financial penalty clause, liquidated damages, indemnification, termination rights, bond forfeiture, personal liability exposure
- high:     breach of contract consequences, acceleration clauses, foreclosure triggers, safety violation consequences
- medium:   standard risk allocation clauses, insurance requirements, warranty limitations, schedule delay provisions
- low:      procedural non-compliance risks, administrative penalties, minor reporting failures

OBLIGATION TYPES (use one): payment, submission, notification, compliance, inspection

RISK TYPES (use one): financial, schedule, quality, safety, compliance, contractual

INSTRUCTIONS:
1. Read the entire contract text carefully.
2. Extract only items that clearly impose a duty on a named or implied party UNDER THIS SPECIFIC AGREEMENT.
3. Extract only risk clauses that clearly expose a party to liability, penalty, or financial loss UNDER THIS SPECIFIC AGREEMENT.
4. Do NOT extract from appended statutory disclosure sections, tenant rights summaries, regulatory notices, or government-mandated informational appendices. These sections merely restate existing law and do not create obligations between the contracting parties.
5. Do NOT extract general statements of law, landlord-tenant ordinance summaries (e.g. RLTO), or "know your rights" sections — even if they use mandatory language like "must" or "shall."
6. Use verbatim quotes from the text (exact wording, not paraphrased).
7. Assign severity using the definitions above. Be decisive.
8. For risks, provide a short reasoning string explaining the severity assignment.

Return ONLY valid JSON in this exact shape — no prose before or after:
{{
  "obligations": [
    {{
      "quote": "<verbatim text from contract>",
      "obligation_type": "<payment|submission|notification|compliance|inspection>",
      "modality": "<shall|must|will|should|may>",
      "severity": "<low|medium|high|critical>",
      "page_hint": <integer page number or null>
    }}
  ],
  "risks": [
    {{
      "quote": "<verbatim text from contract>",
      "risk_type": "<financial|schedule|quality|safety|compliance|contractual>",
      "severity": "<low|medium|high|critical>",
      "reasoning": "<one sentence explaining severity>"
    }}
  ]
}}

CONTRACT TEXT (pages {first_page}–{last_page}):
{full_text}"""


def _fetch_full_text(db, doc_id: uuid.UUID) -> tuple[str, int, int]:
    """Return (full_text, first_page, last_page) from all chunks in order."""
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


def generate(document_id: str, output_dir: Path, model: str = "claude-sonnet-4-6") -> Path:
    doc_id = uuid.UUID(document_id)
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == doc_id).first()
        if not document:
            raise ValueError(f"Document {doc_id} not found in database")

        logger.info("Fetching all chunks for document %s (%s)", doc_id, document.doc_type.value)
        full_text, first_page, last_page = _fetch_full_text(db, doc_id)
        logger.info("Full text: %d chars across pages %d–%d", len(full_text), first_page, last_page)
    finally:
        db.close()

    prompt = _GT_PROMPT.format(
        first_page=first_page,
        last_page=last_page,
        full_text=full_text,
    )

    logger.info("Calling %s for ground truth labeling…", model)
    raw = llm_completion(model, prompt, prefer_json_object=True)
    data = parse_json_dict(raw)

    obligations = data.get("obligations") or []
    risks = data.get("risks") or []
    logger.info("Ground truth: %d obligations, %d risks", len(obligations), len(risks))

    out_path = output_dir / str(doc_id) / "ground_truth.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "document_id": str(doc_id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "obligations": obligations,
        "risks": risks,
    }
    out_path.write_text(json.dumps(result, indent=2))
    logger.info("Ground truth written to %s", out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AI ground truth labels for a document")
    parser.add_argument("--document-id", required=True, help="UUID of the processed document")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "benchmarks"),
        help="Directory to write ground truth JSON (default: backend/data/benchmarks)",
    )
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Model to use for labeling")
    args = parser.parse_args()

    out_path = generate(
        document_id=args.document_id,
        output_dir=Path(args.output_dir),
        model=args.model,
    )
    print(f"Ground truth saved: {out_path}")


if __name__ == "__main__":
    main()
