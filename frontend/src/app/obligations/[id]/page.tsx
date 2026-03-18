"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
import { getCurrentUser, getDocumentPage, getObligation, reviewObligation } from "@/lib/api";
import type { CurrentUser, DocumentPage, ObligationDetail, ReviewDecision } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function buildEvidenceKey(documentId: string, pageNumber: number): string {
  return `${documentId}:${pageNumber}`;
}

function buildContextSnippet(rawText: string, start: number, end: number): string {
  const safeStart = Math.max(0, Math.min(start, rawText.length));
  const safeEnd = Math.max(safeStart, Math.min(end, rawText.length));
  const contextPad = 120;
  const from = Math.max(0, safeStart - contextPad);
  const to = Math.min(rawText.length, safeEnd + contextPad);
  return rawText.slice(from, to).replace(/\s+/g, " ").trim();
}

export default function ObligationEvidencePage() {
  const { getToken } = useAuth();
  const params = useParams<{ id: string }>();
  const obligationId = useMemo(() => params.id, [params.id]);

  const [obligation, setObligation] = useState<ObligationDetail | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [pageContextByKey, setPageContextByKey] = useState<Record<string, DocumentPage>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [initialDecision, setInitialDecision] = useState<ReviewDecision>("approve");

  const loadPageContext = useCallback(
    async (nextObligation: ObligationDetail) => {
      const keys = new Set(nextObligation.evidence.map((item) => buildEvidenceKey(item.document_id, item.page_number)));
      const entries = await Promise.all(
        Array.from(keys).map(async (key) => {
          const [documentId, pageNumberText] = key.split(":");
          const pageNumber = Number(pageNumberText);
          try {
            const page = await getDocumentPage(getToken, documentId, pageNumber);
            return [key, page] as const;
          } catch {
            return null;
          }
        }),
      );

      const nextMap: Record<string, DocumentPage> = {};
      for (const entry of entries) {
        if (!entry) {
          continue;
        }
        nextMap[entry[0]] = entry[1];
      }
      setPageContextByKey(nextMap);
    },
    [getToken],
  );

  const loadObligation = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [payload, user] = await Promise.all([
        getObligation(getToken, obligationId),
        getCurrentUser(getToken),
      ]);
      setCurrentUser(user);
      setObligation(payload);
      await loadPageContext(payload);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Failed to load obligation";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [getToken, loadPageContext, obligationId]);

  useEffect(() => {
    void loadObligation();
  }, [loadObligation]);

  async function submitReview(payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
  }) {
    if (!obligation || !currentUser) {
      throw new Error("Missing review context");
    }
    const response = await reviewObligation(getToken, obligation.id, {
      ...payload,
      reviewer_id: currentUser.id,
    });

    setObligation((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        ...response.obligation,
      };
    });
  }

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">P1 Screen</p>
            <h1 className="text-2xl font-semibold text-slate-900">Obligation Evidence Viewer</h1>
            <p className="text-sm text-slate-600">Obligation ID: {obligationId}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/obligations" className="rounded-full border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700">
              Back to Obligations
            </Link>
            {obligation ? (
              <Link
                href={`/documents/${obligation.document_id}`}
                className="rounded-full bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white"
              >
                Open Document
              </Link>
            ) : null}
          </div>
        </header>

        {isLoading ? <p className="text-sm text-slate-600">Loading obligation evidence...</p> : null}
        {error ? <p className="mb-4 rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p> : null}

        {!isLoading && !error && obligation ? (
          <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
            <section className="space-y-4">
              <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Obligation</p>
                <h2 className="mt-2 text-lg font-semibold text-slate-900">{obligation.obligation_text}</h2>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={obligation.severity} />
                  <StatusBadge status={obligation.status} />
                  <span className="rounded-full border border-slate-300 px-2 py-0.5 text-xs font-semibold text-slate-700">
                    Confidence: {obligation.system_confidence}
                  </span>
                </div>
              </article>

              <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-900">Evidence ({obligation.evidence.length})</p>
                  <a
                    href={`${API_BASE}/documents/${obligation.document_id}/pdf?processed=true`}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
                  >
                    Open PDF
                  </a>
                </div>

                <div className="space-y-3">
                  {obligation.evidence.map((item) => {
                    const key = buildEvidenceKey(item.document_id, item.page_number);
                    const pageContext = pageContextByKey[key];
                    const snippet = pageContext
                      ? buildContextSnippet(pageContext.raw_text, item.raw_char_start, item.raw_char_end)
                      : null;

                    return (
                      <div key={item.id} className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
                          <span className="rounded-full bg-white px-2 py-0.5 font-semibold">Page {item.page_number}</span>
                          <span className="rounded-full bg-white px-2 py-0.5 font-semibold">Source: {item.source}</span>
                          <span className="rounded-full bg-white px-2 py-0.5 font-semibold">
                            Raw chars: {item.raw_char_start}-{item.raw_char_end}
                          </span>
                        </div>
                        <p className="rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-900">{item.quote}</p>
                        <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Context</p>
                        <p className="mt-1 text-sm text-slate-700">
                          {snippet ?? "Context unavailable for this evidence page."}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </article>
            </section>

            <aside className="space-y-4">
              <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="text-sm font-semibold text-slate-900">Item Details</p>
                <dl className="mt-3 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Type</dt>
                    <dd className="font-semibold text-slate-900">{obligation.obligation_type}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Modality</dt>
                    <dd className="font-semibold text-slate-900">{obligation.modality}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Due Kind</dt>
                    <dd className="font-semibold text-slate-900">{obligation.due_kind}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Due Date</dt>
                    <dd className="font-semibold text-slate-900">{obligation.due_date ? obligation.due_date.slice(0, 10) : "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">External Ref</dt>
                    <dd className="font-semibold text-slate-900">{obligation.has_external_reference ? "Yes" : "No"}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Contradiction</dt>
                    <dd className="font-semibold text-slate-900">{obligation.contradiction_flag ? "Yes" : "No"}</dd>
                  </div>
                </dl>
              </article>

              <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="text-sm font-semibold text-slate-900">Review Actions</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    onClick={() => {
                      setInitialDecision("approve");
                      setReviewOpen(true);
                    }}
                    className="rounded-full bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => {
                      setInitialDecision("reject");
                      setReviewOpen(true);
                    }}
                    className="rounded-full border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700"
                  >
                    Reject
                  </button>
                </div>
              </article>
            </aside>
          </div>
        ) : null}
      </div>

      <ReviewModal
        open={reviewOpen}
        title={obligation?.obligation_text ?? ""}
        initialDecision={initialDecision}
        onClose={() => setReviewOpen(false)}
        onSubmit={submitReview}
      />
    </main>
  );
}
