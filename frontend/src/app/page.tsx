"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { createAsset, deleteAsset, getAssets, getCurrentUser, getObligations } from "@/lib/api";
import type { Asset, CurrentUser } from "@/lib/types";

type AssetCard = Asset & { pendingReviews: number };

export default function Home() {
  const { getToken } = useAuth();
  const [assets, setAssets] = useState<AssetCard[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deletingAssetId, setDeletingAssetId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadData() {
      setIsLoading(true);
      setError(null);
      try {
        const [user, assetResponse] = await Promise.all([
          getCurrentUser(getToken),
          getAssets(getToken),
        ]);
        if (!cancelled) setCurrentUser(user);
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

  async function handleCreateAsset(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim() || !currentUser) return;
    setCreating(true);
    setCreateError(null);
    try {
      const asset = await createAsset(getToken, {
        name: newName.trim(),
        description: newDescription.trim() || undefined,
        created_by: currentUser.id,
      });
      setAssets((prev) => [{ ...asset, pendingReviews: 0 }, ...prev]);
      setShowCreateModal(false);
      setNewName("");
      setNewDescription("");
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create asset");
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteAsset(assetId: string) {
    if (!confirm("Delete this asset and all associated documents and extracted data?")) return;
    setDeletingAssetId(assetId);
    setError(null);
    try {
      await deleteAsset(getToken, assetId);
      setAssets((prev) => prev.filter((asset) => asset.id !== assetId));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete asset");
    } finally {
      setDeletingAssetId(null);
    }
  }

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="font-serif text-3xl text-text-primary">Asset Review Queue</h1>
            <p className="mt-1 text-sm text-text-secondary">Choose an asset to open obligations and risks review tables.</p>
          </div>
          {currentUser?.role === "admin" && (
            <button
              onClick={() => setShowCreateModal(true)}
              className="rounded-full border border-border px-4 py-2 text-sm text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
            >
              + New Asset
            </button>
          )}
        </header>

        {isLoading ? <p className="text-sm text-text-secondary">Loading assets...</p> : null}
        {error ? (
          <p className="rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        {!isLoading && !error ? (
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {assets.map((asset) => (
              <article
                key={asset.id}
                className="relative rounded-2xl border border-border bg-surface p-5 shadow-sm transition-colors hover:border-border-strong hover:bg-bg-subtle"
              >
                <Link href={`/assets/${asset.id}/documents`} className="absolute inset-0 rounded-2xl" aria-label={asset.name} />
                <h2 className="text-base font-medium text-text-primary">{asset.name}</h2>
                <p className="mt-1.5 text-sm text-text-secondary">{asset.description || "No description provided."}</p>

                <div className="mt-4 flex items-center justify-between gap-2">
                  <div className="flex gap-2 text-xs">
                    <span
                      style={{ background: "var(--accent-subtle)", color: "var(--accent)", borderColor: "var(--accent)" }}
                      className="rounded-full border px-2 py-1 font-medium"
                    >
                      {asset.pendingReviews} pending
                    </span>
                    <span className="rounded-full border border-border bg-bg-subtle px-2 py-1 font-medium text-text-secondary">
                      {asset.document_count ?? 0} docs
                    </span>
                  </div>
                  <div className="relative z-10 flex items-center gap-2">
                    <Link
                      href={`/assets/${asset.id}/documents`}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
                    >
                      Documents
                    </Link>
                    {currentUser?.role === "admin" ? (
                      <button
                        type="button"
                        aria-label={`Delete ${asset.name}`}
                        title={`Delete ${asset.name}`}
                        onClick={() => void handleDeleteAsset(asset.id)}
                        disabled={deletingAssetId === asset.id}
                        className="rounded-full border p-1.5 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                        style={{ borderColor: "var(--danger)", background: "var(--danger-subtle)", color: "var(--danger)" }}
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden="true"
                        >
                          <path d="M3 6h18" />
                          <path d="M8 6V4h8v2" />
                          <path d="M19 6l-1 14H6L5 6" />
                          <path d="M10 11v6" />
                          <path d="M14 11v6" />
                        </svg>
                      </button>
                    ) : null}
                  </div>
                </div>
              </article>
            ))}
          </section>
        ) : null}
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-brand/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-surface p-6 shadow-2xl">
            <h2 className="font-serif text-xl text-text-primary">New Asset</h2>
            <form onSubmit={(e) => void handleCreateAsset(e)} className="mt-5 space-y-4">
              <div>
                <label className="block text-xs font-medium uppercase tracking-widest text-text-tertiary">Name *</label>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  required
                  className="mt-2 w-full rounded-xl border border-border bg-bg-subtle px-3 py-2 text-sm text-text-primary outline-none transition-colors focus:border-border-strong"
                  placeholder="e.g. Tower Block A"
                />
              </div>
              <div>
                <label className="block text-xs font-medium uppercase tracking-widest text-text-tertiary">Description</label>
                <textarea
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  rows={3}
                  className="mt-2 w-full rounded-xl border border-border bg-bg-subtle px-3 py-2 text-sm text-text-primary outline-none transition-colors focus:border-border-strong"
                  placeholder="Optional description"
                />
              </div>
              {createError && (
                <p className="rounded-lg bg-danger-subtle px-3 py-2 text-xs text-danger">{createError}</p>
              )}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => { setShowCreateModal(false); setNewName(""); setNewDescription(""); setCreateError(null); }}
                  className="rounded-full border border-border px-4 py-2 text-sm text-text-secondary transition-colors hover:text-text-primary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating || !newName.trim()}
                  className="rounded-full bg-brand px-4 py-2 text-sm font-medium text-bg disabled:opacity-50"
                >
                  {creating ? "Creating..." : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}
