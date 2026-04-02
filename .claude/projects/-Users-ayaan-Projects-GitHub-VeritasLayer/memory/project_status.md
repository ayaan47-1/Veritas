---
name: project_status
description: Pipeline implementation state and next milestone
type: project
---

As of 2026-04-02, the MVP is end-to-end functional:

- Backend: deterministic 13-stage pipeline orchestrated by Inngest; PDF parsing + OCR for scanned pages; extraction (entities/obligations/risks), verification, scoring, and LLM-based severity re-scoring.
- Domain intelligence: construction + real estate + financial + general domain profiles with domain-aware classification, extraction vocab, scoring alignment bonuses, and rescore personas.
- API: document processing endpoints (including re-process + delete cascade), document domain surfaced on outputs.
- Frontend (Next.js + Clerk): implemented review UI, obligations/risks tables with sorting, CSV export, document detail + PDF/evidence viewer with confidence breakdown + context digest, domain badge + filtering, notifications bell, and admin screens (users + config overrides).
- Tests: 120 tests passing (up from 54 earlier in the MVP), including new AI regression tests.

Current focus / next milestone:
- Polish and harden the frontend review workflows (bulk triage, reviewer notes), and continue expanding regression coverage around domain classification + extraction.
