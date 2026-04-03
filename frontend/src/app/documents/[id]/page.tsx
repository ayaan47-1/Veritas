"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
import { computeProgressPercent, isInProgressParseStatus } from "@/lib/pipeline";
import {
  getCurrentUser,
  getDocument,
  getDocumentStatus,
  getObligations,
  getRisks,
  reviewObligation,
  reviewRisk,
} from "@/lib/api";
import type { CurrentUser, DocumentDetail, DocumentStatus, Obligation, ReviewDecision, Risk } from "@/lib/types";

type ActiveTab = "obligations" | "risks";

const SEVERITY_ORDER = { critical: 4, high: 3, medium: 2, low: 1 } as const;
const STATUS_ORDER = { needs_review: 3, confirmed: 2, rejected: 1 } as const;

type ObSortKey = "obligation_type" | "severity" | "status" | "confidence" | "due_date";
type RiskSortKey = "risk_type" | "severity" | "status" | "confidence";

function SortHeader<K extends string>({
  label,
  sortKey,
  active,
  dir,
  onToggle,
}: {
  label: string;
  sortKey: K;
  active: boolean;
  dir: "asc" | "desc";
  onToggle: (key: K) => void;
}) {
  return (
    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">
      <button
        onClick={() => onToggle(sortKey)}
        className="flex items-center gap-1 transition-colors hover:text-text-primary"
      >
        {label}
        <span className={active ? "text-text-primary" : "opacity-40 text-text-tertiary"}>
          {active && dir === "asc" ? "↑" : "↓"}
        </span>
      </button>
    </th>
  );
}

export default function DocumentDetailPage() {
  const { getToken } = useAuth();
  const params = useParams<{ id: string }>();
  const documentId = useMemo(() => params.id, [params.id]);

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [activeTab, setActiveTab] = useState<ActiveTab>("obligations");

  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [obligationsNextCursor, setObligationsNextCursor] = useState<string | null>(null);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [risksNextCursor, setRisksNextCursor] = useState<string | null>(null);

  const [obSortKey, setObSortKey] = useState<ObSortKey>("severity");
  const [obSortDir, setObSortDir] = useState<"asc" | "desc">("desc");
  const [riskSortKey, setRiskSortKey] = useState<RiskSortKey>("severity");
  const [riskSortDir, setRiskSortDir] = useState<"asc" | "desc">("desc");

  const [obligationTarget, setObligationTarget] = useState<Obligation | null>(null);
  const [riskTarget, setRiskTarget] = useState<Risk | null>(null);
  const [initialDecision, setInitialDecision] = useState<ReviewDecision>("approve");

  const [isLoading, setIsLoading] = useState(true);
  const [isItemsLoading, setIsItemsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [itemsError, setItemsError] = useState<string | null>(null);

  const loadObligations = useCallback(
    async (doc: DocumentDetail, cursor: string | number, append: boolean) => {
      const response = await getObligations(getToken, {
        assetId: doc.asset_id,
        documentId: doc.id,
        limit: 20,
        cursor,
      });
      setObligations((prev) => (append ? [...prev, ...response.items] : response.items));
      setObligationsNextCursor(response.next_cursor);
    },
    [getToken],
  );

  const loadRisks = useCallback(
    async (doc: DocumentDetail, cursor: string | number, append: boolean) => {
      const response = await getRisks(getToken, {
        assetId: doc.asset_id,
        documentId: doc.id,
        limit: 20,
        cursor,
      });
      setRisks((prev) => (append ? [...prev, ...response.items] : response.items));
      setRisksNextCursor(response.next_cursor);
    },
    [getToken],
  );

  const loadDocumentContext = useCallback(async () => {
    setError(null);
    setItemsError(null);
    setIsLoading(true);
    try {
      const loadedDocument = await getDocument(getToken, documentId);
      setDocument(loadedDocument);
      setStatus(null);
      setObligations([]);
      setRisks([]);
      setObligationsNextCursor(null);
      setRisksNextCursor(null);
      setIsLoading(false);

      void getCurrentUser(getToken)
        .then(setUser)
        .catch(() => {
          // review actions stay disabled until user loads
        });

      void getDocumentStatus(getToken, documentId)
        .then(setStatus)
        .catch(() => {
          // keep page usable even if initial status call fails
        });

      setIsItemsLoading(true);
      const [obResult, riskResult] = await Promise.allSettled([
        loadObligations(loadedDocument, 0, false),
        loadRisks(loadedDocument, 0, false),
      ]);
      if (obResult.status === "rejected" || riskResult.status === "rejected") {
        setItemsError("Could not load extracted items yet. Processing may still be running.");
      }
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Failed to load document detail";
      setError(message);
    } finally {
      setIsLoading(false);
      setIsItemsLoading(false);
    }
  }, [documentId, getToken, loadObligations, loadRisks]);

  useEffect(() => {
    void loadDocumentContext();
  }, [loadDocumentContext]);

  useEffect(() => {
    if (!documentId) {
      return;
    }
    const poll = setInterval(() => {
      void getDocumentStatus(getToken, documentId)
        .then(setStatus)
        .catch(() => {
          // avoid interrupting UI on transient polling errors
        });
    }, 3000);
    return () => clearInterval(poll);
  }, [documentId, getToken]);

  async function submitReview(payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
  }) {
    if (!user) {
      throw new Error("Current user not loaded");
    }

    if (obligationTarget) {
      const response = await reviewObligation(getToken, obligationTarget.id, {
        ...payload,
        reviewer_id: user.id,
      });
      setObligations((prev) => prev.map((item) => (item.id === obligationTarget.id ? response.obligation : item)));
      return;
    }

    if (riskTarget) {
      const response = await reviewRisk(getToken, riskTarget.id, {
        ...payload,
        reviewer_id: user.id,
      });
      setRisks((prev) => prev.map((item) => (item.id === riskTarget.id ? response.risk : item)));
      return;
    }

    throw new Error("Missing review target");
  }

  function toggleObSort(key: ObSortKey) {
    if (obSortKey === key) setObSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setObSortKey(key); setObSortDir("desc"); }
  }

  function toggleRiskSort(key: RiskSortKey) {
    if (riskSortKey === key) setRiskSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setRiskSortKey(key); setRiskSortDir("desc"); }
  }

  const sortedObligations = useMemo(() => {
    return [...obligations].sort((a, b) => {
      let cmp = 0;
      if (obSortKey === "severity") cmp = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
      else if (obSortKey === "status") cmp = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
      else if (obSortKey === "obligation_type") cmp = a.obligation_type.localeCompare(b.obligation_type);
      else if (obSortKey === "due_date") cmp = (a.due_date ?? "").localeCompare(b.due_date ?? "");
      else if (obSortKey === "confidence") {
        const aConf = a.llm_quality_confidence ?? a.system_confidence;
        const bConf = b.llm_quality_confidence ?? b.system_confidence;
        cmp = aConf - bConf;
      }
      return obSortDir === "desc" ? -cmp : cmp;
    });
  }, [obligations, obSortKey, obSortDir]);

  const sortedRisks = useMemo(() => {
    return [...risks].sort((a, b) => {
      let cmp = 0;
      if (riskSortKey === "severity") cmp = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
      else if (riskSortKey === "status") cmp = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
      else if (riskSortKey === "risk_type") cmp = a.risk_type.localeCompare(b.risk_type);
      else if (riskSortKey === "confidence") {
        const aConf = a.llm_quality_confidence ?? a.system_confidence;
        const bConf = b.llm_quality_confidence ?? b.system_confidence;
        cmp = aConf - bConf;
      }
      return riskSortDir === "desc" ? -cmp : cmp;
    });
  }, [risks, riskSortKey, riskSortDir]);

  const totalPages = status?.total_pages ?? document?.total_pages ?? null;
  const progressPercent = computeProgressPercent(status, document?.parse_status);
  const showProgress = status ? isInProgressParseStatus(status.parse_status) : false;

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-2xl text-text-primary">{document?.source_name ?? "Document Detail"}</h1>
            <p className="mt-1 font-mono text-xs text-text-tertiary">{documentId}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {document ? (
              <Link
                href={`/assets/${document.asset_id}/documents`}
                className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary"
              >
                Back to Documents
              </Link>
            ) : null}
            {document ? (
              <Link
                href={`/obligations?asset_id=${document.asset_id}`}
                className="rounded-full bg-brand px-3 py-1.5 text-sm font-medium text-bg"
              >
                Asset Queue
              </Link>
            ) : null}
          </div>
        </header>

        {status ? (
          <section className="mb-5 rounded-2xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">Processing Status — polling every 3s</p>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
              <StatusBadge status={status.parse_status === "complete" ? "confirmed" : status.parse_status === "failed" ? "rejected" : "needs_review"} />
              <span className="text-text-secondary">parse_status: <span className="font-mono text-text-primary">{status.parse_status}</span></span>
            </div>
            {showProgress ? (
              <div className="mt-3">
                <div className="mb-1.5 flex items-center justify-between text-xs text-text-secondary">
                  <span>Job Progress</span>
                  <span className="font-mono text-text-primary">{progressPercent}%</span>
                </div>
                <div className="h-4 overflow-hidden rounded-full border border-border bg-bg-subtle">
                  <div
                    className="h-full rounded-full shadow-sm transition-all duration-500"
                    style={{
                      width: `${progressPercent}%`,
                      background: "#FBBF24",
                    }}
                  />
                </div>
              </div>
            ) : null}
            <details className="mt-3 rounded-xl border border-border bg-bg-subtle px-3 py-2">
              <summary className="cursor-pointer select-none text-xs font-medium uppercase tracking-wider text-text-secondary">
                Stats For Nerds
              </summary>
              <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-text-secondary">
                <span>pages_processed: <span className="font-mono text-text-primary">{status.pages_processed}</span></span>
                <span>pages_failed: <span className="font-mono text-text-primary">{status.pages_failed}</span></span>
                <span>total_pages: <span className="font-mono text-text-primary">{totalPages ?? "—"}</span></span>
                <span>
                  pages_complete:{" "}
                  <span className="font-mono text-text-primary">
                    {status.pages_processed + status.pages_failed}/{status.total_pages ?? "—"}
                  </span>
                </span>
              </div>
            </details>
          </section>
        ) : null}

        {isLoading ? <p className="text-sm text-text-secondary">Loading document detail...</p> : null}
        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}
        {itemsError ? (
          <p className="mb-4 rounded-xl bg-warning-subtle px-4 py-3 text-sm font-medium text-warning">{itemsError}</p>
        ) : null}

        {!isLoading && !error && document ? (
          <>
            <section className="mb-5 rounded-2xl border border-border bg-surface p-4 shadow-sm">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <p className="text-sm text-text-secondary">Doc Type: <span className="font-medium text-text-primary">{document.doc_type}</span></p>
                <p className="text-sm text-text-secondary">Domain: <span className="font-medium text-text-primary">{document.domain ?? "—"}</span></p>
                <p className="text-sm text-text-secondary">Parse Status: <span className="font-medium text-text-primary">{document.parse_status}</span></p>
                <p className="text-sm text-text-secondary">Scanned Pages: <span className="font-medium text-text-primary">{document.scanned_page_count}</span></p>
                <p className="text-sm text-text-secondary">Uploaded: <span className="font-medium text-text-primary">{document.uploaded_at ? document.uploaded_at.replace("T", " ").slice(0, 19) : "—"}</span></p>
              </div>
            </section>

            <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
              <div className="border-b border-border px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => setActiveTab("obligations")}
                    className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                      activeTab === "obligations"
                        ? "bg-brand text-bg"
                        : "border border-border text-text-secondary hover:text-text-primary"
                    }`}
                  >
                    Obligations ({obligations.length})
                  </button>
                  <button
                    onClick={() => setActiveTab("risks")}
                    className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                      activeTab === "risks"
                        ? "bg-brand text-bg"
                        : "border border-border text-text-secondary hover:text-text-primary"
                    }`}
                  >
                    Risks ({risks.length})
                  </button>
                </div>
              </div>

              {activeTab === "obligations" ? (
                <div>
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border bg-bg-subtle">
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Obligation</th>
                        <SortHeader label="Type" sortKey="obligation_type" active={obSortKey === "obligation_type"} dir={obSortDir} onToggle={toggleObSort} />
                        <SortHeader label="Severity" sortKey="severity" active={obSortKey === "severity"} dir={obSortDir} onToggle={toggleObSort} />
                        <SortHeader label="Status" sortKey="status" active={obSortKey === "status"} dir={obSortDir} onToggle={toggleObSort} />
                        <SortHeader label="Confidence" sortKey="confidence" active={obSortKey === "confidence"} dir={obSortDir} onToggle={toggleObSort} />
                        <SortHeader label="Due Date" sortKey="due_date" active={obSortKey === "due_date"} dir={obSortDir} onToggle={toggleObSort} />
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Evidence</th>
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {obligations.length === 0 ? (
                        <tr className="border-t border-border">
                          <td colSpan={8} className="px-4 py-8 text-center text-sm text-text-secondary">
                            {isItemsLoading
                              ? "Loading obligations..."
                              : showProgress
                                ? "Pipeline is still running. Obligations will appear shortly."
                                : "No obligations found for this document."}
                          </td>
                        </tr>
                      ) : (
                        sortedObligations.map((item) => (
                          <tr key={item.id} className="border-t border-border align-top transition-colors hover:bg-bg-subtle">
                            <td className="max-w-xl px-4 py-3 text-text-primary">{item.obligation_text}</td>
                            <td className="px-4 py-3 text-text-secondary">{item.obligation_type}</td>
                            <td className="px-4 py-3">
                              <SeverityBadge severity={item.severity} llmSeverity={item.llm_severity} />
                            </td>
                            <td className="px-4 py-3">
                              <StatusBadge status={item.status} />
                            </td>
                            <td className="px-4 py-3 text-text-secondary">
                              {item.llm_quality_confidence != null ? (
                                <span title={`System: ${item.system_confidence}, LLM quality: ${item.llm_quality_confidence}`}>
                                  {item.llm_quality_confidence}
                                </span>
                              ) : (
                                item.system_confidence
                              )}
                            </td>
                            <td className="px-4 py-3 text-text-secondary">{item.due_date ? item.due_date.slice(0, 10) : "—"}</td>
                            <td className="px-4 py-3">
                              <Link
                                href={`/obligations/${item.id}`}
                                style={{ background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" }}
                                className="rounded-full border px-2.5 py-1 text-xs font-medium"
                              >
                                View
                              </Link>
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex flex-wrap gap-2">
                                <button
                                  onClick={() => {
                                    setInitialDecision("approve");
                                    setRiskTarget(null);
                                    setObligationTarget(item);
                                  }}
                                  style={{ background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" }}
                                  className="rounded-full border px-2.5 py-1 text-xs font-medium"
                                >
                                  Approve
                                </button>
                                <button
                                  onClick={() => {
                                    setInitialDecision("reject");
                                    setRiskTarget(null);
                                    setObligationTarget(item);
                                  }}
                                  style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                                  className="rounded-full border px-2.5 py-1 text-xs font-medium"
                                >
                                  Reject
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                  {obligationsNextCursor ? (
                    <div className="border-t border-border p-3">
                      <button
                        onClick={() => {
                          if (!document) {
                            return;
                          }
                          void loadObligations(document, obligationsNextCursor, true);
                        }}
                        className="rounded-full border border-border px-3 py-1.5 text-xs text-text-secondary transition-colors hover:text-text-primary"
                      >
                        Load More
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div>
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border bg-bg-subtle">
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Risk</th>
                        <SortHeader label="Type" sortKey="risk_type" active={riskSortKey === "risk_type"} dir={riskSortDir} onToggle={toggleRiskSort} />
                        <SortHeader label="Severity" sortKey="severity" active={riskSortKey === "severity"} dir={riskSortDir} onToggle={toggleRiskSort} />
                        <SortHeader label="Status" sortKey="status" active={riskSortKey === "status"} dir={riskSortDir} onToggle={toggleRiskSort} />
                        <SortHeader label="Confidence" sortKey="confidence" active={riskSortKey === "confidence"} dir={riskSortDir} onToggle={toggleRiskSort} />
                        <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {risks.length === 0 ? (
                        <tr className="border-t border-border">
                          <td colSpan={6} className="px-4 py-8 text-center text-sm text-text-secondary">
                            {isItemsLoading
                              ? "Loading risks..."
                              : showProgress
                                ? "Pipeline is still running. Risks will appear shortly."
                                : "No risks found for this document."}
                          </td>
                        </tr>
                      ) : (
                        sortedRisks.map((item) => (
                          <tr key={item.id} className="border-t border-border align-top transition-colors hover:bg-bg-subtle">
                            <td className="max-w-xl px-4 py-3 text-text-primary">{item.risk_text}</td>
                            <td className="px-4 py-3 text-text-secondary">{item.risk_type}</td>
                            <td className="px-4 py-3">
                              <SeverityBadge severity={item.severity} llmSeverity={item.llm_severity} />
                            </td>
                            <td className="px-4 py-3">
                              <StatusBadge status={item.status} />
                            </td>
                            <td className="px-4 py-3 text-text-secondary">
                              {item.llm_quality_confidence != null ? (
                                <span title={`System: ${item.system_confidence}, LLM quality: ${item.llm_quality_confidence}`}>
                                  {item.llm_quality_confidence}
                                </span>
                              ) : (
                                item.system_confidence
                              )}
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex flex-wrap gap-2">
                                <button
                                  onClick={() => {
                                    setInitialDecision("approve");
                                    setObligationTarget(null);
                                    setRiskTarget(item);
                                  }}
                                  style={{ background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" }}
                                  className="rounded-full border px-2.5 py-1 text-xs font-medium"
                                >
                                  Approve
                                </button>
                                <button
                                  onClick={() => {
                                    setInitialDecision("reject");
                                    setObligationTarget(null);
                                    setRiskTarget(item);
                                  }}
                                  style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                                  className="rounded-full border px-2.5 py-1 text-xs font-medium"
                                >
                                  Reject
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                  {risksNextCursor ? (
                    <div className="border-t border-border p-3">
                      <button
                        onClick={() => {
                          if (!document) {
                            return;
                          }
                          void loadRisks(document, risksNextCursor, true);
                        }}
                        className="rounded-full border border-border px-3 py-1.5 text-xs text-text-secondary transition-colors hover:text-text-primary"
                      >
                        Load More
                      </button>
                    </div>
                  ) : null}
                </div>
              )}
            </section>
          </>
        ) : null}
      </div>

      <ReviewModal
        open={Boolean(obligationTarget || riskTarget)}
        title={obligationTarget?.obligation_text ?? riskTarget?.risk_text ?? ""}
        initialDecision={initialDecision}
        itemType={riskTarget ? "risk" : "obligation"}
        initialValues={
          riskTarget
            ? { text: riskTarget.risk_text, severity: riskTarget.severity, risk_type: riskTarget.risk_type }
            : obligationTarget
              ? { text: obligationTarget.obligation_text, severity: obligationTarget.severity }
              : undefined
        }
        onClose={() => {
          setObligationTarget(null);
          setRiskTarget(null);
        }}
        onSubmit={submitReview}
      />
    </main>
  );
}
