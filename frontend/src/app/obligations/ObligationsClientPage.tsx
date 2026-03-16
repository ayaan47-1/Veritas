"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
import { getCurrentUser, getObligations, reviewObligation } from "@/lib/api";
import type { CurrentUser, Obligation, ReviewDecision } from "@/lib/types";

export default function ObligationsClientPage() {
  const { getToken } = useAuth();
  const searchParams = useSearchParams();
  const assetId = useMemo(() => searchParams.get("asset_id"), [searchParams]);

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [items, setItems] = useState<Obligation[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewTarget, setReviewTarget] = useState<Obligation | null>(null);
  const [initialDecision, setInitialDecision] = useState<ReviewDecision>("approve");

  const loadPage = useCallback(
    async (cursor: string | number, append: boolean) => {
      if (!assetId) {
        setError("Missing asset_id query parameter.");
        setIsLoading(false);
        return;
      }
      try {
        const [currentUser, obligations] = await Promise.all([
          getCurrentUser(getToken),
          getObligations(getToken, { assetId, cursor, limit: 20 }),
        ]);
        setUser(currentUser);
        setItems((prev) => (append ? [...prev, ...obligations.items] : obligations.items));
        setNextCursor(obligations.next_cursor);
      } catch (loadError) {
        const message = loadError instanceof Error ? loadError.message : "Failed to load obligations";
        setError(message);
      } finally {
        setIsLoading(false);
      }
    },
    [assetId, getToken],
  );

  useEffect(() => {
    setItems([]);
    setNextCursor(null);
    setError(null);
    setIsLoading(true);
    void loadPage(0, false);
  }, [loadPage]);

  async function submitReview(payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
  }) {
    if (!reviewTarget || !user) {
      throw new Error("Missing review context");
    }
    const response = await reviewObligation(getToken, reviewTarget.id, {
      ...payload,
      reviewer_id: user.id,
    });
    setItems((prev) => prev.map((item) => (item.id === reviewTarget.id ? response.obligation : item)));
  }

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">P0 Screen</p>
            <h1 className="text-2xl font-semibold text-slate-900">Obligations Table</h1>
            <p className="text-sm text-slate-600">Asset: {assetId ?? "not selected"}</p>
          </div>
          <div className="flex gap-2">
            <Link href="/" className="rounded-full border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700">
              Assets
            </Link>
            <Link
              href={assetId ? `/risks?asset_id=${assetId}` : "/risks"}
              className="rounded-full bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white"
            >
              Risks
            </Link>
          </div>
        </header>

        {isLoading ? <p className="text-sm text-slate-600">Loading obligations...</p> : null}
        {error ? <p className="mb-4 rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p> : null}

        {!isLoading && !error ? (
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full border-collapse text-sm">
              <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-200">
                <tr>
                  <th className="px-4 py-3">Obligation</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Severity</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Due Date</th>
                  <th className="px-4 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
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
                      <div className="flex flex-wrap gap-2">
                        <button
                          onClick={() => {
                            setInitialDecision("approve");
                            setReviewTarget(item);
                          }}
                          className="rounded-full bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => {
                            setInitialDecision("reject");
                            setReviewTarget(item);
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

            {nextCursor ? (
              <div className="border-t border-slate-100 p-3">
                <button
                  onClick={() => void loadPage(nextCursor, true)}
                  className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
                >
                  Load More
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>

      <ReviewModal
        open={Boolean(reviewTarget)}
        title={reviewTarget?.obligation_text ?? ""}
        initialDecision={initialDecision}
        onClose={() => setReviewTarget(null)}
        onSubmit={submitReview}
      />
    </main>
  );
}
