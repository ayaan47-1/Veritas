"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
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

  const [obligationTarget, setObligationTarget] = useState<Obligation | null>(null);
  const [riskTarget, setRiskTarget] = useState<Risk | null>(null);
  const [initialDecision, setInitialDecision] = useState<ReviewDecision>("approve");

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    setIsLoading(true);
    try {
      const [currentUser, loadedDocument, loadedStatus] = await Promise.all([
        getCurrentUser(getToken),
        getDocument(getToken, documentId),
        getDocumentStatus(getToken, documentId),
      ]);
      setUser(currentUser);
      setDocument(loadedDocument);
      setStatus(loadedStatus);
      await Promise.all([
        loadObligations(loadedDocument, 0, false),
        loadRisks(loadedDocument, 0, false),
      ]);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Failed to load document detail";
      setError(message);
    } finally {
      setIsLoading(false);
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

  const totalPages = status?.total_pages ?? document?.total_pages ?? null;

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">P1 Screen</p>
            <h1 className="text-2xl font-semibold text-slate-900">{document?.source_name ?? "Document Detail"}</h1>
            <p className="text-sm text-slate-600">Document ID: {documentId}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {document ? (
              <Link
                href={`/assets/${document.asset_id}/documents`}
                className="rounded-full border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700"
              >
                Back to Documents
              </Link>
            ) : null}
            {document ? (
              <Link
                href={`/obligations?asset_id=${document.asset_id}`}
                className="rounded-full bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white"
              >
                Asset Queue
              </Link>
            ) : null}
          </div>
        </header>

        {status ? (
          <section className="mb-5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Processing Status (polling every 3s)</p>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
              <StatusBadge status={status.parse_status === "complete" ? "confirmed" : status.parse_status === "failed" ? "rejected" : "needs_review"} />
              <span className="text-slate-700">parse_status: {status.parse_status}</span>
              <span className="text-slate-700">pages_processed: {status.pages_processed}</span>
              <span className="text-slate-700">pages_failed: {status.pages_failed}</span>
              <span className="text-slate-700">total_pages: {totalPages ?? "—"}</span>
            </div>
          </section>
        ) : null}

        {isLoading ? <p className="text-sm text-slate-600">Loading document detail...</p> : null}
        {error ? <p className="mb-4 rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p> : null}

        {!isLoading && !error && document ? (
          <>
            <section className="mb-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <p className="text-sm text-slate-700">Doc Type: <span className="font-semibold">{document.doc_type}</span></p>
                <p className="text-sm text-slate-700">Parse Status: <span className="font-semibold">{document.parse_status}</span></p>
                <p className="text-sm text-slate-700">Scanned Pages: <span className="font-semibold">{document.scanned_page_count}</span></p>
                <p className="text-sm text-slate-700">Uploaded: <span className="font-semibold">{document.uploaded_at ? document.uploaded_at.replace("T", " ").slice(0, 19) : "—"}</span></p>
              </div>
            </section>

            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-200 px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => setActiveTab("obligations")}
                    className={`rounded-full px-3 py-1.5 text-sm font-semibold ${
                      activeTab === "obligations" ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700"
                    }`}
                  >
                    Obligations ({obligations.length})
                  </button>
                  <button
                    onClick={() => setActiveTab("risks")}
                    className={`rounded-full px-3 py-1.5 text-sm font-semibold ${
                      activeTab === "risks" ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700"
                    }`}
                  >
                    Risks ({risks.length})
                  </button>
                </div>
              </div>

              {activeTab === "obligations" ? (
                <div>
                  <table className="w-full border-collapse text-sm">
                    <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-200">
                      <tr>
                        <th className="px-4 py-3">Obligation</th>
                        <th className="px-4 py-3">Type</th>
                        <th className="px-4 py-3">Severity</th>
                        <th className="px-4 py-3">Status</th>
                        <th className="px-4 py-3">Due Date</th>
                        <th className="px-4 py-3">Evidence</th>
                        <th className="px-4 py-3">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {obligations.map((item) => (
                        <tr key={item.id} className="border-t border-slate-100 align-top">
                          <td className="max-w-xl px-4 py-3 text-slate-900">{item.obligation_text}</td>
                          <td className="px-4 py-3 text-slate-600">{item.obligation_type}</td>
                          <td className="px-4 py-3">
                            <SeverityBadge severity={item.severity} />
                          </td>
                          <td className="px-4 py-3">
                            <StatusBadge status={item.status} />
                          </td>
                          <td className="px-4 py-3 text-slate-600">{item.due_date ? item.due_date.slice(0, 10) : "—"}</td>
                          <td className="px-4 py-3">
                            <Link
                              href={`/obligations/${item.id}`}
                              className="rounded-full border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700"
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
                                className="rounded-full bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white"
                              >
                                Approve
                              </button>
                              <button
                                onClick={() => {
                                  setInitialDecision("reject");
                                  setRiskTarget(null);
                                  setObligationTarget(item);
                                }}
                                className="rounded-full border border-rose-300 bg-white px-2.5 py-1 text-xs font-semibold text-rose-700"
                              >
                                Reject
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {obligationsNextCursor ? (
                    <div className="border-t border-slate-100 p-3">
                      <button
                        onClick={() => {
                          if (!document) {
                            return;
                          }
                          void loadObligations(document, obligationsNextCursor, true);
                        }}
                        className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
                      >
                        Load More
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div>
                  <table className="w-full border-collapse text-sm">
                    <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-200">
                      <tr>
                        <th className="px-4 py-3">Risk</th>
                        <th className="px-4 py-3">Type</th>
                        <th className="px-4 py-3">Severity</th>
                        <th className="px-4 py-3">Status</th>
                        <th className="px-4 py-3">Confidence</th>
                        <th className="px-4 py-3">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {risks.map((item) => (
                        <tr key={item.id} className="border-t border-slate-100 align-top">
                          <td className="max-w-xl px-4 py-3 text-slate-900">{item.risk_text}</td>
                          <td className="px-4 py-3 text-slate-600">{item.risk_type}</td>
                          <td className="px-4 py-3">
                            <SeverityBadge severity={item.severity} />
                          </td>
                          <td className="px-4 py-3">
                            <StatusBadge status={item.status} />
                          </td>
                          <td className="px-4 py-3 text-slate-600">{item.system_confidence}</td>
                          <td className="px-4 py-3">
                            <div className="flex flex-wrap gap-2">
                              <button
                                onClick={() => {
                                  setInitialDecision("approve");
                                  setObligationTarget(null);
                                  setRiskTarget(item);
                                }}
                                className="rounded-full bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white"
                              >
                                Approve
                              </button>
                              <button
                                onClick={() => {
                                  setInitialDecision("reject");
                                  setObligationTarget(null);
                                  setRiskTarget(item);
                                }}
                                className="rounded-full border border-rose-300 bg-white px-2.5 py-1 text-xs font-semibold text-rose-700"
                              >
                                Reject
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {risksNextCursor ? (
                    <div className="border-t border-slate-100 p-3">
                      <button
                        onClick={() => {
                          if (!document) {
                            return;
                          }
                          void loadRisks(document, risksNextCursor, true);
                        }}
                        className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
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
        onClose={() => {
          setObligationTarget(null);
          setRiskTarget(null);
        }}
        onSubmit={submitReview}
      />
    </main>
  );
}
