"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
import { getCurrentUser, getDocument, getDocumentPage, getRisk, reviewRisk } from "@/lib/api";
import { buildContextDigest, formatQuoteAsProse } from "@/lib/evidence-utils";
import type { CurrentUser, DocumentDetail, DocumentPage, RiskDetail, ReviewDecision } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

function buildEvidenceKey(documentId: string, pageNumber: number): string {
  return `${documentId}:${pageNumber}`;
}

export default function RiskEvidencePage() {
  const { getToken } = useAuth();
  const params = useParams<{ id: string }>();
  const riskId = useMemo(() => params.id, [params.id]);

  const [risk, setRisk] = useState<RiskDetail | null>(null);
  const [documentDetail, setDocumentDetail] = useState<DocumentDetail | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [pageContextByKey, setPageContextByKey] = useState<Record<string, DocumentPage>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [initialDecision, setInitialDecision] = useState<ReviewDecision>("approve");

  const loadPageContext = useCallback(
    async (nextRisk: RiskDetail) => {
      const keys = new Set(nextRisk.evidence.map((item) => buildEvidenceKey(item.document_id, item.page_number)));
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
        if (!entry) continue;
        nextMap[entry[0]] = entry[1];
      }
      setPageContextByKey(nextMap);
    },
    [getToken],
  );

  const loadRisk = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [payload, user] = await Promise.all([
        getRisk(getToken, riskId),
        getCurrentUser(getToken),
      ]);
      const doc = await getDocument(getToken, payload.document_id);
      setCurrentUser(user);
      setRisk(payload);
      setDocumentDetail(doc);
      await loadPageContext(payload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load risk");
    } finally {
      setIsLoading(false);
    }
  }, [getToken, loadPageContext, riskId]);

  useEffect(() => {
    void loadRisk();
  }, [loadRisk]);

  const displayedConfidence = risk?.llm_quality_confidence ?? risk?.system_confidence ?? 0;
  const confidenceLabel = risk?.llm_quality_confidence != null ? "LLM quality" : "System confidence";
  const confidenceTitle =
    risk && risk.llm_quality_confidence != null
      ? `LLM quality confidence ${risk.llm_quality_confidence}. System score ${risk.system_confidence}.`
      : "Rule-based system confidence from verification and scoring signals.";

  async function submitReview(payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
  }) {
    if (!risk || !currentUser) throw new Error("Missing review context");
    const response = await reviewRisk(getToken, risk.id, {
      ...payload,
      reviewer_id: currentUser.id,
    });
    setRisk((prev) => {
      if (!prev) return prev;
      return { ...prev, ...response.risk };
    });
  }

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-2xl text-text-primary">Risk Evidence</h1>
            <p className="mt-1 font-mono text-xs text-text-tertiary">{riskId}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/risks" className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary">
              Back to Risks
            </Link>
            {risk ? (
              <Link
                href={`/documents/${risk.document_id}`}
                className="rounded-full bg-brand px-3 py-1.5 text-sm font-medium text-bg"
              >
                Open Document
              </Link>
            ) : null}
          </div>
        </header>

        {isLoading ? <p className="text-sm text-text-secondary">Loading risk evidence...</p> : null}
        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        {!isLoading && !error && risk ? (
          <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
            <section className="space-y-4">
              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <h2 className="font-serif text-lg leading-relaxed text-text-primary">{risk.risk_text}</h2>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={risk.severity} llmSeverity={risk.llm_severity} />
                  <StatusBadge status={risk.status} />
                  <span
                    style={{ background: "var(--bg-subtle)", color: "var(--text-secondary)", borderColor: "var(--border)" }}
                    className="rounded-full border px-2 py-0.5 text-xs font-medium"
                    title={confidenceTitle}
                  >
                    {confidenceLabel}: {displayedConfidence}
                  </span>
                </div>
              </article>

              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <p className="text-sm font-medium text-text-primary">Evidence ({risk.evidence.length})</p>
                  <a
                    href={`${API_BASE}/documents/${risk.document_id}/pdf?processed=true`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" }}
                    className="rounded-full border px-3 py-1.5 text-xs font-medium"
                  >
                    Open PDF
                  </a>
                </div>

                {risk.evidence.length === 0 ? (
                  <p className="text-sm text-text-secondary">No evidence anchored for this risk.</p>
                ) : (
                  <div className="space-y-4">
                    {risk.evidence.map((item) => {
                      const key = buildEvidenceKey(item.document_id, item.page_number);
                      const pageContext = pageContextByKey[key];
                      const contextDigest = pageContext
                        ? buildContextDigest(pageContext.raw_text, item.raw_char_start, item.raw_char_end, item.quote)
                        : null;
                      const quoteFormat = formatQuoteAsProse(item.quote);

                      return (
                        <div key={item.id} className="rounded-xl border border-border bg-bg-subtle p-4">
                          {/* Page metadata */}
                          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
                            <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-medium">Page {item.page_number}</span>
                            <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-medium">Source: {item.source}</span>
                            <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-mono">
                              chars {item.raw_char_start}–{item.raw_char_end}
                            </span>
                          </div>

                          {/* Quote — formatted as paragraph or bullet list */}
                          <div className="rounded-lg border-l-2 border-border-strong bg-surface px-4 py-3">
                            <p className="mb-1 text-xs font-medium uppercase tracking-wider text-text-tertiary">Quote</p>
                            {quoteFormat.type === "bullets" ? (
                              <ul className="space-y-1 text-sm leading-relaxed text-text-primary">
                                {quoteFormat.items.map((bullet, i) => (
                                  <li key={i} className="flex gap-2">
                                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-text-tertiary" />
                                    <span>{bullet}</span>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <p className="text-sm leading-relaxed text-text-primary">{quoteFormat.text}</p>
                            )}
                          </div>

                          {/* Context — always visible when available */}
                          {contextDigest && contextDigest.bullets.length > 0 ? (
                            <div className="mt-3 rounded-lg border border-border bg-surface px-4 py-3">
                              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-tertiary">Surrounding Context</p>
                              <ul className="space-y-1.5 text-sm leading-relaxed text-text-secondary">
                                {contextDigest.bullets.map((bullet, index) => (
                                  <li key={`${item.id}-bullet-${index}`} className="flex gap-2">
                                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-text-tertiary" />
                                    <span>{bullet}</span>
                                  </li>
                                ))}
                              </ul>
                              {contextDigest.references.length > 0 ? (
                                <div className="mt-3 flex flex-wrap gap-2">
                                  {contextDigest.references.map((reference) => (
                                    <span
                                      key={`${item.id}-${reference}`}
                                      className="rounded-full border border-border bg-bg-subtle px-2 py-1 text-xs font-medium text-text-secondary"
                                    >
                                      {reference}
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                )}
              </article>
            </section>

            <aside className="space-y-4">
              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm font-medium text-text-primary">Item Details</p>
                <dl className="mt-3 space-y-0 divide-y divide-border text-sm">
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Type</dt>
                    <dd className="font-medium text-text-primary">{risk.risk_type}</dd>
                  </div>
                  {documentDetail ? (
                    <div className="flex justify-between gap-3 py-2">
                      <dt className="text-text-tertiary">Document</dt>
                      <dd className="truncate font-medium text-text-primary">{documentDetail.source_name}</dd>
                    </div>
                  ) : null}
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">External Ref</dt>
                    <dd className="font-medium text-text-primary">{risk.has_external_reference ? "Yes" : "No"}</dd>
                  </div>
                  <div className="flex justify-between gap-3 py-2">
                    <dt className="text-text-tertiary">Contradiction</dt>
                    <dd className="font-medium text-text-primary">{risk.contradiction_flag ? "Yes" : "No"}</dd>
                  </div>
                </dl>
              </article>

              <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm font-medium text-text-primary">Review Actions</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    onClick={() => { setInitialDecision("approve"); setReviewOpen(true); }}
                    style={{ background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" }}
                    className="rounded-full border px-3 py-1.5 text-xs font-medium"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => { setInitialDecision("reject"); setReviewOpen(true); }}
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
        title={risk?.risk_text ?? ""}
        initialDecision={initialDecision}
        itemType="risk"
        initialValues={risk ? { text: risk.risk_text, severity: risk.severity, risk_type: risk.risk_type } : undefined}
        onClose={() => setReviewOpen(false)}
        onSubmit={submitReview}
      />
    </main>
  );
}
