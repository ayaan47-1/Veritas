"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { getAssets, getObligations } from "@/lib/api";
import type { Asset } from "@/lib/types";

type AssetCard = Asset & { pendingReviews: number };

export default function Home() {
  const { getToken } = useAuth();
  const [assets, setAssets] = useState<AssetCard[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadData() {
      setIsLoading(true);
      setError(null);
      try {
        const assetResponse = await getAssets(getToken);
        const cards = await Promise.all(
          assetResponse.items.map(async (asset) => {
            try {
              const pending = await getObligations(getToken, {
                assetId: asset.id,
                status: "needs_review",
                limit: 200,
                cursor: 0,
              });
              return { ...asset, pendingReviews: pending.items.length };
            } catch {
              return { ...asset, pendingReviews: 0 };
            }
          }),
        );
        if (!cancelled) {
          setAssets(cards);
        }
      } catch (loadError) {
        if (!cancelled) {
          const message = loadError instanceof Error ? loadError.message : "Could not load assets";
          setError(message);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }
    void loadData();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_#f8fafc_0%,_#e2e8f0_45%,_#cbd5e1_100%)] px-6 py-10">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 rounded-3xl bg-white/80 p-6 shadow-sm ring-1 ring-slate-200 backdrop-blur">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">VeritasLayer</p>
          <h1 className="mt-2 text-3xl font-semibold text-slate-900">Asset Review Queue</h1>
          <p className="mt-2 text-sm text-slate-600">Choose an asset to open obligations and risks review tables.</p>
        </header>

        {isLoading ? <p className="text-sm text-slate-600">Loading assets...</p> : null}
        {error ? <p className="rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p> : null}

        {!isLoading && !error ? (
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {assets.map((asset) => (
              <article
                key={asset.id}
                className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
              >
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Asset</p>
                <h2 className="mt-1 text-xl font-semibold text-slate-900">{asset.name}</h2>
                <p className="mt-2 text-sm text-slate-600">{asset.description || "No description provided."}</p>

                <div className="mt-4 flex gap-2 text-xs">
                  <span className="rounded-full bg-slate-100 px-2 py-1 font-semibold text-slate-700">
                    Pending: {asset.pendingReviews}
                  </span>
                  <span className="rounded-full bg-cyan-100 px-2 py-1 font-semibold text-cyan-800">
                    Docs: {asset.document_count ?? "-"}
                  </span>
                </div>

                <div className="mt-5 flex gap-2">
                  <Link
                    href={`/obligations?asset_id=${asset.id}`}
                    className="rounded-full bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white"
                  >
                    Obligations
                  </Link>
                  <Link
                    href={`/risks?asset_id=${asset.id}`}
                    className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
                  >
                    Risks
                  </Link>
                </div>
              </article>
            ))}
          </section>
        ) : null}
      </div>
    </main>
  );
}
