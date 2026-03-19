---
name: project_status
description: Pipeline implementation state and next milestone
type: project
---

All 11 backend pipeline stages implemented with TDD. 54 tests passing. Clerk JWT auth integrated on the backend.

**Why:** MVP is complete on the backend side; frontend is next.

**How to apply:** Frontend work is the current focus.

Frontend: Next.js (App Router, TypeScript) with `@clerk/nextjs` — switched from SvelteKit (not available). Located at `frontend/`. Needs scaffolding via `npx create-next-app@latest`.

Next milestone: Scaffold Next.js frontend, add Clerk middleware + ClerkProvider, build obligations review UI.
