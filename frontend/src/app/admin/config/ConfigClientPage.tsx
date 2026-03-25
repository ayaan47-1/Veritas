"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

import { deleteConfigOverride, getConfig, getCurrentUser, upsertConfigOverride } from "@/lib/api";
import type { ConfigResponse, CurrentUser } from "@/lib/types";

type Tab = "overrides" | "effective";

export default function ConfigClientPage() {
  const { getToken } = useAuth();

  const [me, setMe] = useState<CurrentUser | null>(null);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overrides");

  // Add/edit override form
  const [formKey, setFormKey] = useState("");
  const [formValue, setFormValue] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Edit mode: which override key is being edited
  const [editingKey, setEditingKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [currentUser, configResp] = await Promise.all([
        getCurrentUser(getToken),
        getConfig(getToken),
      ]);
      setMe(currentUser);
      setConfig(configResp);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load config");
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    void load();
  }, [load]);

  function startEdit(key: string, value: unknown) {
    setEditingKey(key);
    setFormKey(key);
    setFormValue(JSON.stringify(value, null, 2));
    setFormError(null);
  }

  function startAdd() {
    setEditingKey(null);
    setFormKey("");
    setFormValue("{}");
    setFormError(null);
  }

  async function submitOverride() {
    if (!me || !formKey.trim()) {
      setFormError("Key is required.");
      return;
    }
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(formValue) as Record<string, unknown>;
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
        throw new Error("Value must be a JSON object.");
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Invalid JSON.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await upsertConfigOverride(getToken, formKey.trim(), parsed, me.id);
      // Reload to get fresh effective config
      const fresh = await getConfig(getToken);
      setConfig(fresh);
      setEditingKey(null);
      setFormKey("");
      setFormValue("{}");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save override");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(key: string) {
    if (!confirm(`Delete override for "${key}"?`)) return;
    try {
      await deleteConfigOverride(getToken, key);
      const fresh = await getConfig(getToken);
      setConfig(fresh);
      if (editingKey === key) setEditingKey(null);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete override");
    }
  }

  if (isLoading) {
    return <main className="min-h-screen bg-slate-50 px-6 py-10 text-sm text-slate-600">Loading config...</main>;
  }

  if (me && me.role !== "admin") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50">
        <p className="text-sm font-medium text-slate-500">Access denied — admin only.</p>
      </main>
    );
  }

  const overrides = config?.overrides ?? {};
  const overrideKeys = Object.keys(overrides);

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">Admin</p>
            <h1 className="text-2xl font-semibold text-slate-900">Config</h1>
            <p className="text-sm text-slate-500">
              {overrideKeys.length} active {overrideKeys.length === 1 ? "override" : "overrides"}
            </p>
          </div>
          <Link
            href="/admin/users"
            className="rounded-full border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700"
          >
            Users
          </Link>
        </header>

        {error ? (
          <p className="mb-4 rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p>
        ) : null}

        {/* Tabs */}
        <div className="mb-4 flex gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm w-fit">
          {(["overrides", "effective"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-4 py-1.5 text-sm font-semibold capitalize transition-colors ${
                tab === t ? "bg-slate-900 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              {t === "overrides" ? "Overrides" : "Effective Config"}
            </button>
          ))}
        </div>

        {tab === "overrides" ? (
          <div className="space-y-4">
            {/* Existing overrides */}
            {overrideKeys.length > 0 ? (
              <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <table className="w-full border-collapse text-sm">
                  <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-200">
                    <tr>
                      <th className="px-4 py-3">Key</th>
                      <th className="px-4 py-3">Value</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overrideKeys.map((key) => (
                      <tr key={key} className="border-t border-slate-100 align-top">
                        <td className="px-4 py-3 font-mono text-xs font-semibold text-slate-800">{key}</td>
                        <td className="max-w-sm px-4 py-3">
                          <pre className="overflow-x-auto rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700">
                            {JSON.stringify(overrides[key], null, 2)}
                          </pre>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            <button
                              onClick={() => startEdit(key, overrides[key])}
                              className="rounded-full border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-slate-400"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => void handleDelete(key)}
                              className="rounded-full border border-rose-300 px-2.5 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-50"
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ) : (
              <p className="text-sm text-slate-500">No overrides active. Base config is in effect.</p>
            )}

            {/* Add/Edit form */}
            {editingKey !== undefined ? (
              <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <h2 className="mb-4 text-sm font-semibold text-slate-900">
                  {editingKey !== null ? `Edit override: ${editingKey}` : "Add override"}
                </h2>
                <div className="space-y-3">
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-slate-600">
                      Key <span className="font-normal text-slate-400">(dotted path, e.g. llm or scoring.weights)</span>
                    </label>
                    <input
                      type="text"
                      value={formKey}
                      onChange={(e) => setFormKey(e.target.value)}
                      disabled={editingKey !== null}
                      placeholder="llm"
                      className="w-full rounded-xl border border-slate-300 px-3 py-2 font-mono text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-cyan-500 disabled:bg-slate-50 disabled:text-slate-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-slate-600">
                      Value <span className="font-normal text-slate-400">(JSON object)</span>
                    </label>
                    <textarea
                      value={formValue}
                      onChange={(e) => setFormValue(e.target.value)}
                      rows={8}
                      spellCheck={false}
                      className="w-full rounded-xl border border-slate-300 px-3 py-2 font-mono text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-cyan-500"
                    />
                  </div>
                  {formError ? (
                    <p className="rounded-xl bg-rose-100 px-3 py-2 text-xs font-medium text-rose-700">{formError}</p>
                  ) : null}
                  <div className="flex gap-2">
                    <button
                      onClick={() => void submitOverride()}
                      disabled={saving}
                      className="rounded-full bg-cyan-700 px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
                    >
                      {saving ? "Saving…" : "Save Override"}
                    </button>
                    <button
                      onClick={() => {
                        setEditingKey(undefined as unknown as string | null);
                        setFormKey("");
                        setFormValue("{}");
                        setFormError(null);
                      }}
                      className="rounded-full border border-slate-300 px-4 py-1.5 text-sm font-semibold text-slate-700"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </section>
            ) : (
              <button
                onClick={startAdd}
                className="rounded-full bg-slate-900 px-4 py-1.5 text-sm font-semibold text-white"
              >
                + Add Override
              </button>
            )}
          </div>
        ) : (
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-100 px-4 py-3">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                Merged result of base config + active overrides
              </p>
            </div>
            <pre className="overflow-x-auto px-6 py-5 text-xs text-slate-700 leading-relaxed">
              {JSON.stringify(config?.effective ?? {}, null, 2)}
            </pre>
          </section>
        )}
      </div>
    </main>
  );
}
