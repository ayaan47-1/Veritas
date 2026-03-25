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

  // Role editing
  const [editingRoleFor, setEditingRoleFor] = useState<string | null>(null);
  const [pendingRole, setPendingRole] = useState<User["role"] | "">("");
  const [savingRole, setSavingRole] = useState(false);

  // Asset management modal
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
    return <main className="min-h-screen bg-slate-50 px-6 py-10 text-sm text-slate-600">Loading users...</main>;
  }

  if (me && me.role !== "admin") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50">
        <p className="text-sm font-medium text-slate-500">Access denied — admin only.</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">Admin</p>
            <h1 className="text-2xl font-semibold text-slate-900">Users</h1>
            <p className="text-sm text-slate-500">{users.length} {users.length === 1 ? "user" : "users"}</p>
          </div>
        </header>

        {error ? (
          <p className="mb-4 rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p>
        ) : null}

        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-200">
              <tr>
                <th className="px-4 py-3">User</th>
                <th className="px-4 py-3">Role</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Last Login</th>
                <th className="px-4 py-3">Assets</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-t border-slate-100 align-middle">
                  <td className="px-4 py-3">
                    <p className="font-medium text-slate-900">{user.name || "—"}</p>
                    <p className="text-xs text-slate-500">{user.email}</p>
                  </td>
                  <td className="px-4 py-3">
                    {editingRoleFor === user.id ? (
                      <div className="flex items-center gap-2">
                        <select
                          value={pendingRole}
                          onChange={(e) => setPendingRole(e.target.value as User["role"])}
                          className="rounded-lg border border-slate-300 px-2 py-1 text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-cyan-500"
                        >
                          <option value="viewer">viewer</option>
                          <option value="reviewer">reviewer</option>
                          <option value="admin">admin</option>
                        </select>
                        <button
                          onClick={() => void saveRole(user.id)}
                          disabled={savingRole}
                          className="rounded-full bg-cyan-700 px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50"
                        >
                          {savingRole ? "Saving…" : "Save"}
                        </button>
                        <button
                          onClick={() => setEditingRoleFor(null)}
                          className="text-xs text-slate-500 hover:text-slate-700"
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
                        className="flex items-center gap-1.5 group"
                        title="Click to edit role"
                      >
                        <RoleBadge role={user.role} />
                        <span className="text-xs text-slate-400 opacity-0 group-hover:opacity-100">edit</span>
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${
                        user.is_active
                          ? "border-emerald-300 bg-emerald-100 text-emerald-800"
                          : "border-slate-300 bg-slate-100 text-slate-600"
                      }`}
                    >
                      {user.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {user.last_login_at ? user.last_login_at.slice(0, 10) : "Never"}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => void openAssetModal(user)}
                      className="rounded-full border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-slate-400"
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

      {/* Asset management modal */}
      {managingUser ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-xl">
            <div className="border-b border-slate-200 px-6 py-4">
              <p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">Asset Access</p>
              <h2 className="text-base font-semibold text-slate-900">{managingUser.name || managingUser.email}</h2>
            </div>

            <div className="max-h-80 overflow-y-auto px-6 py-4">
              {loadingAssets ? (
                <p className="text-sm text-slate-500">Loading...</p>
              ) : assets.length === 0 ? (
                <p className="text-sm text-slate-500">No assets available.</p>
              ) : (
                <ul className="space-y-2">
                  {assets.map((asset) => {
                    const assigned = userAssets.some((a) => a.asset_id === asset.id);
                    const toggling = togglingAsset === asset.id;
                    return (
                      <li key={asset.id} className="flex items-center justify-between rounded-xl border border-slate-100 px-3 py-2">
                        <div>
                          <p className="text-sm font-medium text-slate-900">{asset.name}</p>
                          {asset.description ? (
                            <p className="text-xs text-slate-500">{asset.description}</p>
                          ) : null}
                        </div>
                        <button
                          onClick={() => void toggleAsset(asset.id)}
                          disabled={toggling}
                          className={`rounded-full px-3 py-1 text-xs font-semibold disabled:opacity-50 ${
                            assigned
                              ? "border border-rose-300 bg-white text-rose-700 hover:bg-rose-50"
                              : "bg-cyan-700 text-white hover:bg-cyan-800"
                          }`}
                        >
                          {toggling ? "…" : assigned ? "Remove" : "Add"}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="border-t border-slate-200 px-6 py-4">
              <button
                onClick={() => {
                  setManagingUser(null);
                  setUserAssets([]);
                }}
                className="w-full rounded-full border border-slate-300 py-2 text-sm font-semibold text-slate-700"
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
