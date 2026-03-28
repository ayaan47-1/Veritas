"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

import RoleBadge from "@/components/RoleBadge";
import {
  assignUserAsset,
  getAssets,
  getCurrentUser,
  getUserAssets,
  getUsers,
  removeUserAsset,
  updateUserRole,
} from "@/lib/api";
import type { Asset, CurrentUser, User, UserAssetAssignment } from "@/lib/types";

export default function UsersClientPage() {
  const { getToken } = useAuth();

  const [me, setMe] = useState<CurrentUser | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editingRoleFor, setEditingRoleFor] = useState<string | null>(null);
  const [pendingRole, setPendingRole] = useState<User["role"] | "">("");
  const [savingRole, setSavingRole] = useState(false);

  const [managingUser, setManagingUser] = useState<User | null>(null);
  const [userAssets, setUserAssets] = useState<UserAssetAssignment[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [togglingAsset, setTogglingAsset] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [currentUser, usersResp, assetsResp] = await Promise.all([
        getCurrentUser(getToken),
        getUsers(getToken),
        getAssets(getToken),
      ]);
      setMe(currentUser);
      setUsers(usersResp.items);
      setAssets(assetsResp.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveRole(userId: string) {
    if (!pendingRole) return;
    setSavingRole(true);
    try {
      const updated = await updateUserRole(getToken, userId, pendingRole as User["role"]);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setEditingRoleFor(null);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update role");
    } finally {
      setSavingRole(false);
    }
  }

  async function openAssetModal(user: User) {
    setManagingUser(user);
    setLoadingAssets(true);
    try {
      const assignments = await getUserAssets(getToken, user.id);
      setUserAssets(assignments);
    } catch {
      setUserAssets([]);
    } finally {
      setLoadingAssets(false);
    }
  }

  async function toggleAsset(assetId: string) {
    if (!managingUser) return;
    setTogglingAsset(assetId);
    const existing = userAssets.find((a) => a.asset_id === assetId);
    try {
      if (existing) {
        await removeUserAsset(getToken, managingUser.id, assetId);
        setUserAssets((prev) => prev.filter((a) => a.asset_id !== assetId));
      } else {
        const assignment = await assignUserAsset(getToken, managingUser.id, assetId);
        setUserAssets((prev) => [...prev, assignment]);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update asset");
    } finally {
      setTogglingAsset(null);
    }
  }

  if (isLoading) {
    return <main className="min-h-screen bg-bg px-6 py-10 text-sm text-text-secondary">Loading users...</main>;
  }

  if (me && me.role !== "admin") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-bg">
        <p className="text-sm font-medium text-text-tertiary">Access denied — admin only.</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-text-tertiary">Admin</p>
            <h1 className="mt-1 font-serif text-2xl text-text-primary">Users</h1>
            <p className="mt-1 text-sm text-text-secondary">{users.length} {users.length === 1 ? "user" : "users"}</p>
          </div>
        </header>

        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-subtle">
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">User</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Role</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Last Login</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Assets</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-t border-border align-middle transition-colors hover:bg-bg-subtle">
                  <td className="px-4 py-3">
                    <p className="font-medium text-text-primary">{user.name || "—"}</p>
                    <p className="text-xs text-text-tertiary">{user.email}</p>
                  </td>
                  <td className="px-4 py-3">
                    {editingRoleFor === user.id ? (
                      <div className="flex items-center gap-2">
                        <select
                          value={pendingRole}
                          onChange={(e) => setPendingRole(e.target.value as User["role"])}
                          className="rounded-lg border border-border bg-surface px-2 py-1 text-xs text-text-primary outline-none focus:border-border-strong"
                        >
                          <option value="viewer">viewer</option>
                          <option value="reviewer">reviewer</option>
                          <option value="admin">admin</option>
                        </select>
                        <button
                          onClick={() => void saveRole(user.id)}
                          disabled={savingRole}
                          className="rounded-full bg-brand px-2.5 py-1 text-xs font-medium text-bg disabled:opacity-50"
                        >
                          {savingRole ? "Saving…" : "Save"}
                        </button>
                        <button
                          onClick={() => setEditingRoleFor(null)}
                          className="text-xs text-text-tertiary hover:text-text-secondary"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => {
                          setEditingRoleFor(user.id);
                          setPendingRole(user.role);
                        }}
                        className="group flex items-center gap-1.5"
                        title="Click to edit role"
                      >
                        <RoleBadge role={user.role} />
                        <span className="text-xs text-text-tertiary opacity-0 group-hover:opacity-100">edit</span>
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      style={
                        user.is_active
                          ? { background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" }
                          : { background: "var(--bg-subtle)", color: "var(--text-tertiary)", borderColor: "var(--border)" }
                      }
                      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium"
                    >
                      {user.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-text-secondary">
                    {user.last_login_at ? user.last_login_at.slice(0, 10) : "Never"}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => void openAssetModal(user)}
                      className="rounded-full border border-border px-2.5 py-1 text-xs text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
                    >
                      Manage Assets
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      {managingUser ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-brand/60 px-4 backdrop-blur-sm">
          <div className="w-full max-w-md overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
            <div className="border-b border-border px-6 py-4">
              <p className="text-xs font-medium uppercase tracking-widest text-text-tertiary">Asset Access</p>
              <h2 className="mt-1 font-serif text-lg text-text-primary">{managingUser.name || managingUser.email}</h2>
            </div>

            <div className="max-h-80 overflow-y-auto px-6 py-4">
              {loadingAssets ? (
                <p className="text-sm text-text-secondary">Loading...</p>
              ) : assets.length === 0 ? (
                <p className="text-sm text-text-secondary">No assets available.</p>
              ) : (
                <ul className="space-y-2">
                  {assets.map((asset) => {
                    const assigned = userAssets.some((a) => a.asset_id === asset.id);
                    const toggling = togglingAsset === asset.id;
                    return (
                      <li key={asset.id} className="flex items-center justify-between rounded-xl border border-border px-3 py-2">
                        <div>
                          <p className="text-sm font-medium text-text-primary">{asset.name}</p>
                          {asset.description ? (
                            <p className="text-xs text-text-tertiary">{asset.description}</p>
                          ) : null}
                        </div>
                        <button
                          onClick={() => void toggleAsset(asset.id)}
                          disabled={toggling}
                          style={
                            assigned
                              ? { background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }
                              : { background: "var(--brand)", color: "var(--bg)" }
                          }
                          className={`rounded-full border px-3 py-1 text-xs font-medium disabled:opacity-50 ${assigned ? "" : "border-transparent"}`}
                        >
                          {toggling ? "…" : assigned ? "Remove" : "Add"}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="border-t border-border px-6 py-4">
              <button
                onClick={() => {
                  setManagingUser(null);
                  setUserAssets([]);
                }}
                className="w-full rounded-full border border-border py-2 text-sm text-text-secondary transition-colors hover:text-text-primary"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
