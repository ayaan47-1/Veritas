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

  const [formKey, setFormKey] = useState("");
  const [formValue, setFormValue] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
    return <main className="min-h-screen bg-bg px-6 py-10 text-sm text-text-secondary">Loading config...</main>;
  }

  if (me && me.role !== "admin") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-bg">
        <p className="text-sm font-medium text-text-tertiary">Access denied — admin only.</p>
      </main>
    );
  }

  const overrides = config?.overrides ?? {};
  const overrideKeys = Object.keys(overrides);

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-5xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-text-tertiary">Admin</p>
            <h1 className="mt-1 font-serif text-2xl text-text-primary">Config</h1>
            <p className="mt-1 text-sm text-text-secondary">
              {overrideKeys.length} active {overrideKeys.length === 1 ? "override" : "overrides"}
            </p>
          </div>
          <Link
            href="/admin/users"
            className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary"
          >
            Users
          </Link>
        </header>

        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        <div className="mb-5 flex w-fit gap-1 rounded-xl border border-border bg-bg-subtle p-1">
          {(["overrides", "effective"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium capitalize transition-colors ${
                tab === t
                  ? "bg-surface text-text-primary shadow-sm"
                  : "text-text-secondary hover:text-text-primary"
              }`}
            >
              {t === "overrides" ? "Overrides" : "Effective Config"}
            </button>
          ))}
        </div>

        {tab === "overrides" ? (
          <div className="space-y-4">
            {overrideKeys.length > 0 ? (
              <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-border bg-bg-subtle">
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Key</th>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Value</th>
                      <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overrideKeys.map((key) => (
                      <tr key={key} className="border-t border-border align-top transition-colors hover:bg-bg-subtle">
                        <td className="px-4 py-3 font-mono text-xs font-medium text-text-primary">{key}</td>
                        <td className="max-w-sm px-4 py-3">
                          <pre className="overflow-x-auto rounded-lg border border-border bg-bg-subtle px-3 py-2 text-xs text-text-secondary">
                            {JSON.stringify(overrides[key], null, 2)}
                          </pre>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            <button
                              onClick={() => startEdit(key, overrides[key])}
                              className="rounded-full border border-border px-2.5 py-1 text-xs text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => void handleDelete(key)}
                              style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                              className="rounded-full border px-2.5 py-1 text-xs font-medium"
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
              <p className="text-sm text-text-secondary">No overrides active. Base config is in effect.</p>
            )}

            {editingKey !== undefined ? (
              <section className="rounded-2xl border border-border bg-surface p-6 shadow-sm">
                <h2 className="mb-4 text-sm font-medium text-text-primary">
                  {editingKey !== null ? `Edit override: ${editingKey}` : "Add override"}
                </h2>
                <div className="space-y-3">
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase tracking-widest text-text-tertiary">
                      Key <span className="normal-case font-normal text-text-tertiary">(dotted path, e.g. llm or scoring.weights)</span>
                    </label>
                    <input
                      type="text"
                      value={formKey}
                      onChange={(e) => setFormKey(e.target.value)}
                      disabled={editingKey !== null}
                      placeholder="llm"
                      className="w-full rounded-xl border border-border bg-bg-subtle px-3 py-2 font-mono text-sm text-text-primary outline-none transition-colors focus:border-border-strong disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase tracking-widest text-text-tertiary">
                      Value <span className="normal-case font-normal text-text-tertiary">(JSON object)</span>
                    </label>
                    <textarea
                      value={formValue}
                      onChange={(e) => setFormValue(e.target.value)}
                      rows={8}
                      spellCheck={false}
                      className="w-full rounded-xl border border-border bg-bg-subtle px-3 py-2 font-mono text-sm text-text-primary outline-none transition-colors focus:border-border-strong"
                    />
                  </div>
                  {formError ? (
                    <p className="rounded-xl bg-danger-subtle px-3 py-2 text-xs font-medium text-danger">{formError}</p>
                  ) : null}
                  <div className="flex gap-2">
                    <button
                      onClick={() => void submitOverride()}
                      disabled={saving}
                      className="rounded-full bg-brand px-4 py-1.5 text-sm font-medium text-bg disabled:opacity-50"
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
                      className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </section>
            ) : (
              <button
                onClick={startAdd}
                className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
              >
                + Add Override
              </button>
            )}
          </div>
        ) : (
          <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
            <div className="border-b border-border px-4 py-3">
              <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
                Merged result of base config + active overrides
              </p>
            </div>
            <pre className="overflow-x-auto px-6 py-5 font-mono text-xs leading-relaxed text-text-secondary">
              {JSON.stringify(config?.effective ?? {}, null, 2)}
            </pre>
          </section>
        )}
      </div>
    </main>
  );
}
