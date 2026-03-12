# VeritasLayer — AI Operational Intelligence Layer (MVP)

VeritasLayer ingests operational documents (starting with PDFs) and produces high-precision, evidence-traceable:

- Obligations and deadlines  
- Risk alerts  
- Weekly summaries derived from structured outputs  

Core guarantee: **no claim without verifiable evidence** (page number, exact quote, and character offsets).

---

# Purpose

VeritasLayer creates a **truth layer** for operational assets such as buildings, construction projects, portfolios, and facilities. It converts unstructured documents into structured, auditable intelligence that software and humans can trust.

This MVP is not a chatbot. Chat interfaces can be built later on top of the verified truth layer.

---

# Core Principles

## Evidence-first
Every obligation or risk must include an evidence object containing:

- document_id  
- page_number  
- quote (verbatim)  
- char_start  
- char_end  
- optional bbox coordinates  

If the quote cannot be anchored exactly in the stored document text, the item cannot be confirmed.

---

## Quote-first extraction

Extraction pipeline:

1. Extract verbatim quotes from the document  
2. Verify the quote exists in stored page text  
3. Convert the quote into structured obligation or risk fields  

This prevents hallucinated claims.

---

## Precision-first gating

Confidence scoring determines status:

- confidence >= 80 → confirmed  
- confidence 50–79 → needs_review  
- confidence < 50 → rejected  

Only strong obligation language such as "must", "shall", or "required" may be confirmed automatically.

---

# Tech Stack

Backend:
- Python 3.11+
- FastAPI
- SQLAlchemy
- Alembic

Database:
- Postgres
- pgvector (recommended)

Parsing:
- PyMuPDF (extract page text and coordinates)

LLM routing:
- LiteLLM (supports OpenAI, Anthropic, Gemini)

Testing:
- pytest
