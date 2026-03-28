"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ReviewModal from "@/components/ReviewModal";
import SeverityBadge from "@/components/SeverityBadge";
import StatusBadge from "@/components/StatusBadge";
import { getAssets, getCurrentUser, getRisks, reviewRisk } from "@/lib/api";
import type { Asset, CurrentUser, ReviewDecision, Risk } from "@/lib/types";

const SEVERITY_ORDER = { critical: 4, high: 3, medium: 2, low: 1 } as const;
const STATUS_ORDER = { needs_review: 3, confirmed: 2, rejected: 1 } as const;

type SortKey = "severity" | "status" | "risk_type" | "system_confidence";

function SortHeader({
  label,
  sortKey,
  active,
  dir,
  onToggle,
}: {
  label: string;
  sortKey: SortKey;
  active: boolean;
  dir: "asc" | "desc";
  onToggle: (key: SortKey) => void;
}) {
  return (
    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">
      <button
        onClick={() => onToggle(sortKey)}
        className="flex items-center gap-1 hover:text-text-primary transition-colors"
      >
        {label}
        <span className={active ? "text-text-primary" : "text-text-tertiary opacity-40"}>
          {active && dir === "asc" ? "↑" : "↓"}
        </span>
      </button>
    </th>
  );
}

export default function RisksClientPage() {
  const { getToken } = useAuth();
  const searchParams = useSearchParams();
  const assetId = useMemo(() => searchParams.get("asset_id"), [searchParams]);

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [items, setItems] = useState<Risk[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewTarget, setReviewTarget] = useState<Risk | null>(null);
  const [initialDecision, setInitialDecision] = useState<ReviewDecision>("approve");
  const [sortKey, setSortKey] = useState<SortKey>("severity");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const selectedAsset = useMemo(() => assets.find((a) => a.id === assetId) ?? null, [assets, assetId]);

  const loadAssets = useCallback(async () => {
    try {
      const [currentUser, response] = await Promise.all([getCurrentUser(getToken), getAssets(getToken)]);
      setUser(currentUser);
      setAssets(response.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load assets");
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const loadRisks = useCallback(
    async (cursor: string | number, append: boolean) => {
      try {
        const [currentUser, response, assetResponse] = await Promise.all([
          getCurrentUser(getToken),
          getRisks(getToken, { assetId: assetId ?? undefined, cursor, limit: 100 }),
          assets.length === 0 ? getAssets(getToken) : Promise.resolve(null),
        ]);
        setUser(currentUser);
        if (assetResponse) setAssets(assetResponse.items);
        setItems((prev) => (append ? [...prev, ...response.items] : response.items));
        setNextCursor(response.next_cursor);
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "Failed to load risks");
      } finally {
        setIsLoading(false);
      }
    },
    [assetId, assets.length, getToken],
  );

  useEffect(() => {
    setItems([]);
    setNextCursor(null);
    setError(null);
    setIsLoading(true);
    if (!assetId) {
      void loadAssets();
    } else {
      void loadRisks(0, false);
    }
  }, [assetId, loadAssets, loadRisks]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "severity") cmp = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
      else if (sortKey === "status") cmp = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
      else if (sortKey === "risk_type") cmp = a.risk_type.localeCompare(b.risk_type);
      else if (sortKey === "system_confidence") cmp = a.system_confidence - b.system_confidence;
      return sortDir === "desc" ? -cmp : cmp;
    });
  }, [items, sortKey, sortDir]);

  async function submitReview(payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
  }) {
    if (!reviewTarget || !user) throw new Error("Missing review context");
    const response = await reviewRisk(getToken, reviewTarget.id, { ...payload, reviewer_id: user.id });
    setItems((prev) => prev.map((item) => (item.id === reviewTarget.id ? response.risk : item)));
  }

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-2xl text-text-primary">Risks</h1>
            <p className="mt-1 text-sm text-text-secondary">
              {assetId ? (selectedAsset?.name ?? assetId) : "Select an asset to review risks"}
            </p>
          </div>
          {assetId ? (
            <div className="flex gap-2">
              <Link href="/risks" className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary">
                ← All Assets
              </Link>
              <Link
                href={`/obligations?asset_id=${assetId}`}
                className="rounded-full bg-brand px-3 py-1.5 text-sm font-medium text-bg"
              >
                Obligations
              </Link>
            </div>
          ) : null}
        </header>

        {isLoading ? <p className="text-sm text-text-secondary">Loading...</p> : null}
        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        {/* Asset selection grid */}
        {!isLoading && !error && !assetId ? (
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {assets.map((asset) => (
              <article
                key={asset.id}
                className="relative rounded-2xl border border-border bg-surface p-5 shadow-sm transition-colors hover:border-border-strong hover:bg-bg-subtle"
              >
                <Link href={`/risks?asset_id=${asset.id}`} className="absolute inset-0 rounded-2xl" aria-label={asset.name} />
                <h2 className="text-base font-medium text-text-primary">{asset.name}</h2>
                <p className="mt-1 text-sm text-text-secondary">{asset.description ?? "No description."}</p>
                <div className="mt-4 flex gap-2 text-xs">
                  <span
                    style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                    className="rounded-full border px-2 py-1 font-medium"
                  >
                    {asset.risk_count ?? "—"} risks
                  </span>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {/* Risks table */}
        {!isLoading && !error && assetId ? (
          <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-subtle">
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Risk</th>
                  <SortHeader label="Type" sortKey="risk_type" active={sortKey === "risk_type"} dir={sortDir} onToggle={toggleSort} />
                  <SortHeader label="Severity" sortKey="severity" active={sortKey === "severity"} dir={sortDir} onToggle={toggleSort} />
                  <SortHeader label="Status" sortKey="status" active={sortKey === "status"} dir={sortDir} onToggle={toggleSort} />
                  <SortHeader label="Confidence" sortKey="system_confidence" active={sortKey === "system_confidence"} dir={sortDir} onToggle={toggleSort} />
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((item) => (
                  <tr key={item.id} className="border-t border-border align-top transition-colors hover:bg-bg-subtle">
                    <td className="max-w-xl px-4 py-3 text-text-primary">{item.risk_text}</td>
                    <td className="px-4 py-3 text-text-secondary">{item.risk_type}</td>
                    <td className="px-4 py-3"><SeverityBadge severity={item.severity} /></td>
                    <td className="px-4 py-3"><StatusBadge status={item.status} /></td>
                    <td className="px-4 py-3 text-text-secondary">{item.system_confidence}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          onClick={() => { setInitialDecision("approve"); setReviewTarget(item); }}
                          style={{ background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" }}
                          className="rounded-full border px-2.5 py-1 text-xs font-medium"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => { setInitialDecision("reject"); setReviewTarget(item); }}
                          style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                          className="rounded-full border px-2.5 py-1 text-xs font-medium"
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
              <div className="border-t border-border p-3">
                <button
                  onClick={() => void loadRisks(nextCursor, true)}
                  className="rounded-full border border-border px-3 py-1.5 text-xs text-text-secondary transition-colors hover:text-text-primary"
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
        title={reviewTarget?.risk_text ?? ""}
        initialDecision={initialDecision}
        itemType="risk"
        initialValues={reviewTarget ? { text: reviewTarget.risk_text, severity: reviewTarget.severity, risk_type: reviewTarget.risk_type } : undefined}
        onClose={() => setReviewTarget(null)}
        onSubmit={submitReview}
      />
    </main>
  );
}
