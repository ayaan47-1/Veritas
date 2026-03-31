"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
import { getCurrentUser, getDocument, getDocumentPage, getObligation, reviewObligation } from "@/lib/api";
import type { CurrentUser, DocumentDetail, DocumentPage, ObligationDetail, ReviewDecision } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const DEADLINE_RE = /\b(by|before|within|no later than|after|days?|weeks?|months?)\b/i;

type ScoreBreakdownItem = {
  label: string;
  delta: number;
};

type ContextDigest = {
  bullets: string[];
  references: string[];
};

function buildEvidenceKey(documentId: string, pageNumber: number): string {
  return `${documentId}:${pageNumber}`;
}

function normalizeInlineText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function canonicalizeText(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

function jaccardSimilarity(a: string, b: string): number {
  const aTokens = new Set(canonicalizeText(a).split(" ").filter(Boolean));
  const bTokens = new Set(canonicalizeText(b).split(" ").filter(Boolean));
  if (aTokens.size === 0 || bTokens.size === 0) {
    return 0;
  }
  let intersection = 0;
  for (const token of aTokens) {
    if (bTokens.has(token)) {
      intersection += 1;
    }
  }
  const union = aTokens.size + bTokens.size - intersection;
  return union > 0 ? intersection / union : 0;
}

function isRedundantWithQuote(contextText: string, quote: string): boolean {
  const canonicalContext = canonicalizeText(contextText);
  const canonicalQuote = canonicalizeText(quote);
  if (!canonicalContext || !canonicalQuote) {
    return false;
  }
  if (canonicalContext === canonicalQuote) {
    return true;
  }
  if (canonicalContext.includes(canonicalQuote) || canonicalQuote.includes(canonicalContext)) {
    return true;
  }
  return jaccardSimilarity(canonicalContext, canonicalQuote) >= 0.8;
}

function isUsefulContextBullet(text: string): boolean {
  const normalized = normalizeInlineText(text);
  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length < 5) {
    return false;
  }
  if (/[(-]$/.test(normalized)) {
    return false;
  }
  if (/^\(?eff\.\s*\d/i.test(normalized) && words.length < 7) {
    return false;
  }
  return true;
}

function buildContextBlocks(rawText: string): string[] {
  const lines = rawText
    .replace(/\r/g, "")
    .replace(/\u2022/g, "\n• ")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const blocks: Array<{ text: string; isBullet: boolean }> = [];

  for (const line of lines) {
    const isBullet = /^[•\-–]/.test(line);
    const cleaned = normalizeInlineText(line.replace(/^[•\-–]+\s*/, ""));
    if (!cleaned) {
      continue;
    }

    if (isBullet || blocks.length === 0) {
      blocks.push({ text: cleaned, isBullet });
      continue;
    }

    const last = blocks[blocks.length - 1];
    last.text = normalizeInlineText(`${last.text} ${cleaned}`);
  }

  return blocks
    .filter((block, index) => block.isBullet || index > 0)
    .map((block) => block.text)
    .filter((block) => block.length > 12);
}

function extractContextReferences(parts: string[]): string[] {
  const matches = parts.flatMap((part) =>
    Array.from(
      part.matchAll(
        /(?:§+\s*\d+(?:\.\d+)*)|(?:section|sec\.|clause|article|paragraph)\s+[A-Za-z0-9.-]+|\b\d+(?:\.\d+)+\b|\$\s*[\d,]+(?:\.\d+)?|\b\d+\s*(?:days?|weeks?|months?|years?)\b|\b\d+(?:\.\d+)?%/gi,
      ),
      (match) => normalizeInlineText(match[0]),
    ),
  );

  return Array.from(new Set(matches));
}

function buildContextDigest(rawText: string, start: number, end: number, quote: string): ContextDigest {
  const normalizedQuote = normalizeInlineText(quote);
  if (!rawText.trim()) {
    return { bullets: [], references: [] };
  }

  const blocks = buildContextBlocks(rawText);
  if (blocks.length === 0) {
    return {
      bullets: [],
      references: normalizedQuote ? extractContextReferences([normalizedQuote]) : [],
    };
  }

  const centerIndex = blocks.findIndex((block) => block.includes(normalizedQuote));
  if (centerIndex < 0) {
    return {
      bullets: [],
      references: normalizedQuote ? extractContextReferences([normalizedQuote]) : [],
    };
  }

  const bullets: string[] = [];
  const previous = blocks[centerIndex - 1];
  const next = blocks[centerIndex + 1];

  if (previous && !isRedundantWithQuote(previous, normalizedQuote) && isUsefulContextBullet(previous)) {
    bullets.push(previous);
  }
  if (next && !isRedundantWithQuote(next, normalizedQuote) && isUsefulContextBullet(next)) {
    bullets.push(next);
  }

  const references = extractContextReferences(bullets);

  return {
    bullets: Array.from(new Set(bullets)),
    references,
  };
}

function obligationDocTypeAligned(docType: string | undefined, obligationType: string): boolean {
  if (!docType) {
    return false;
  }
  if (docType === "invoice") {
    return obligationType === "payment";
  }
  return true;
}

function impliesDeadline(text: string): boolean {
  return DEADLINE_RE.test(text);
}

function buildObligationScoreBreakdown(
  obligation: ObligationDetail,
  evidenceCount: number,
  hasOcrEvidence: boolean,
  document: DocumentDetail | null,
): ScoreBreakdownItem[] {
  if (obligation.status === "rejected" && evidenceCount === 0) {
    return [{ label: "No verified evidence found", delta: 0 }];
  }

  const items: ScoreBreakdownItem[] = [];
  const hasDueRule = Boolean(obligation.due_rule?.trim());

  if (evidenceCount > 0) {
    items.push({ label: "Quote verified against document text", delta: 40 });
    items.push({ label: "Verifier pass", delta: 15 });
  }

  if (["must", "shall", "required"].includes(obligation.modality)) {
    items.push({ label: `Strong modality: ${obligation.modality}`, delta: 15 });
  }

  if (obligation.due_kind === "absolute" || obligation.due_kind === "resolved_relative" || hasDueRule) {
    items.push({ label: "Due date or due rule resolved", delta: 10 });
  }

  if (obligation.responsible_entity_id) {
    items.push({ label: "Responsible party linked", delta: 10 });
  }

  if (obligationDocTypeAligned(document?.doc_type, obligation.obligation_type)) {
    items.push({ label: `Document type aligned: ${document?.doc_type}`, delta: 10 });
  }

  if (/\$[\d,]+|dollar/i.test(obligation.obligation_text)) {
    items.push({ label: "Monetary amount detected", delta: 5 });
  }

  if (/(§|C\.R\.S\.|U\.S\.C\.|statute|regulation)/i.test(obligation.obligation_text)) {
    items.push({ label: "Statute or regulation reference detected", delta: 5 });
  }

  if (["should", "may"].includes(obligation.modality)) {
    items.push({ label: `Weak modality: ${obligation.modality}`, delta: -25 });
  }

  if (hasOcrEvidence) {
    items.push({ label: "Evidence came from OCR text", delta: -15 });
  }

  if (obligation.contradiction_flag) {
    items.push({ label: "Contradiction detected", delta: -30 });
  }

  if (impliesDeadline(obligation.obligation_text) && !obligation.due_date && !hasDueRule) {
    items.push({ label: "Deadline language without parsed due date/rule", delta: -10 });
  }

  return items;
}

export default function ObligationEvidencePage() {
  const { getToken } = useAuth();
  const params = useParams<{ id: string }>();
  const obligationId = useMemo(() => params.id, [params.id]);

  const [obligation, setObligation] = useState<ObligationDetail | null>(null);
  const [documentDetail, setDocumentDetail] = useState<DocumentDetail | null>(null);
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
      const doc = await getDocument(getToken, payload.document_id);
      setCurrentUser(user);
      setObligation(payload);
      setDocumentDetail(doc);
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

  const scoreBreakdown = useMemo(() => {
    if (!obligation) {
      return [];
    }
    return buildObligationScoreBreakdown(
      obligation,
      obligation.evidence.length,
      obligation.evidence.some((item) => item.source === "ocr"),
      documentDetail,
    );
  }, [documentDetail, obligation]);

  const displayedConfidence = obligation?.llm_quality_confidence ?? obligation?.system_confidence ?? 0;
  const confidenceLabel = obligation?.llm_quality_confidence != null ? "LLM quality" : "System confidence";
  const confidenceTitle =
    obligation && obligation.llm_quality_confidence != null
      ? `LLM quality confidence ${obligation.llm_quality_confidence}. System score ${obligation.system_confidence}.`
      : "Rule-based system confidence from verification, modality, deadlines, linked parties, and penalties.";

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
                    title={confidenceTitle}
                  >
                    {confidenceLabel}: {displayedConfidence}
                  </span>
                </div>
                {scoreBreakdown.length > 0 ? (
                  <div className="mt-4 rounded-xl border border-border bg-bg-subtle p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">Confidence Breakdown</p>
                      <span className="text-xs font-semibold text-text-primary">Total: {obligation.system_confidence}</span>
                    </div>
                    <ul className="mt-2 space-y-1.5 text-sm text-text-secondary">
                      {scoreBreakdown.map((item) => (
                        <li key={`${item.label}-${item.delta}`} className="flex items-start justify-between gap-3">
                          <span>{item.label}</span>
                          <span className={`shrink-0 font-mono ${item.delta >= 0 ? "text-green-700" : "text-red-700"}`}>
                            {item.delta >= 0 ? `+${item.delta}` : item.delta}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
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
                    const contextDigest = pageContext
                      ? buildContextDigest(pageContext.raw_text, item.raw_char_start, item.raw_char_end, item.quote)
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
                        {contextDigest && contextDigest.bullets.length > 0 ? (
                          <details className="mt-3 rounded-lg border border-border bg-surface px-3 py-2">
                            <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-text-tertiary">
                              Context Summary
                            </summary>
                            <ul className="mt-2 space-y-1.5 pl-4 text-sm leading-relaxed text-text-secondary">
                              {contextDigest.bullets.map((bullet, index) => (
                                <li key={`${item.id}-bullet-${index}`} className="list-disc">
                                  <span>{bullet}</span>
                                </li>
                              ))}
                            </ul>
                            {contextDigest.references.length > 0 ? (
                              <div className="mt-3">
                                <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">Key References</p>
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {contextDigest.references.map((reference) => (
                                    <span
                                      key={`${item.id}-${reference}`}
                                      className="rounded-full border border-border bg-bg-subtle px-2 py-1 text-xs font-medium text-text-secondary"
                                    >
                                      {reference}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                          </details>
                        ) : null}
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
