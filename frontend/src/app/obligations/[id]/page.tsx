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
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-2xl text-text-primary">Obligation Evidence</h1>
            <p className="mt-1 font-mono text-xs text-text-tertiary">{obligationId}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/obligations" className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary">
              Back to Obligations
            </Link>
            {obligation ? (
              <Link
                href={`/documents/${obligation.document_id}`}
                className="rounded-full bg-brand px-3 py-1.5 text-sm font-medium text-bg"
              >
                Open Document
              </Link>
            ) : null}
          </div>
        </header>

        {isLoading ? <p className="text-sm text-text-secondary">Loading obligation evidence...</p> : null}
        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        {!isLoading && !error && obligation ? (
          <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
            <section className="space-y-4">
              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <h2 className="font-serif text-lg leading-relaxed text-text-primary">{obligation.obligation_text}</h2>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={obligation.severity} />
                  <StatusBadge status={obligation.status} />
                  <span
                    style={{ background: "var(--bg-subtle)", color: "var(--text-secondary)", borderColor: "var(--border)" }}
                    className="rounded-full border px-2 py-0.5 text-xs font-medium"
                  >
                    Confidence: {obligation.system_confidence}
                  </span>
                </div>
              </article>

              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <p className="text-sm font-medium text-text-primary">Evidence ({obligation.evidence.length})</p>
                  <a
                    href={`${API_BASE}/documents/${obligation.document_id}/pdf?processed=true`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" }}
                    className="rounded-full border px-3 py-1.5 text-xs font-medium"
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
                      <div key={item.id} className="rounded-xl border border-border bg-bg-subtle p-4">
                        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
                          <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-medium">Page {item.page_number}</span>
                          <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-medium">Source: {item.source}</span>
                          <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-mono">
                            chars {item.raw_char_start}–{item.raw_char_end}
                          </span>
                        </div>
                        <p className="rounded-lg border border-border bg-surface p-3 font-mono text-sm text-text-primary">{item.quote}</p>
                        <p className="mt-3 text-xs font-medium uppercase tracking-wider text-text-tertiary">Context</p>
                        <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                          {snippet ?? "Context unavailable for this evidence page."}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </article>
            </section>

            <aside className="space-y-4">
              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm font-medium text-text-primary">Item Details</p>
                <dl className="mt-3 space-y-0 divide-y divide-border text-sm">
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Type</dt>
                    <dd className="font-medium text-text-primary">{obligation.obligation_type}</dd>
                  </div>
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Modality</dt>
                    <dd className="font-medium text-text-primary">{obligation.modality}</dd>
                  </div>
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Due Kind</dt>
                    <dd className="font-medium text-text-primary">{obligation.due_kind}</dd>
                  </div>
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Due Date</dt>
                    <dd className="font-medium text-text-primary">{obligation.due_date ? obligation.due_date.slice(0, 10) : "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">External Ref</dt>
                    <dd className="font-medium text-text-primary">{obligation.has_external_reference ? "Yes" : "No"}</dd>
                  </div>
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Contradiction</dt>
                    <dd className="font-medium text-text-primary">{obligation.contradiction_flag ? "Yes" : "No"}</dd>
                  </div>
                </dl>
              </article>

              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm font-medium text-text-primary">Review Actions</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    onClick={() => {
                      setInitialDecision("approve");
                      setReviewOpen(true);
                    }}
                    style={{ background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" }}
                    className="rounded-full border px-3 py-1.5 text-xs font-medium"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => {
                      setInitialDecision("reject");
                      setReviewOpen(true);
                    }}
                    style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                    className="rounded-full border px-3 py-1.5 text-xs font-medium"
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
        itemType="obligation"
        initialValues={obligation ? { text: obligation.obligation_text, severity: obligation.severity } : undefined}
        onClose={() => setReviewOpen(false)}
        onSubmit={submitReview}
      />
    </main>
  );
}
