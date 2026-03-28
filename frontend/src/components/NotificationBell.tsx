"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getCurrentUser, getNotifications, markNotificationRead } from "@/lib/api";
import type { CurrentUser, UserNotification } from "@/lib/types";

function summarizePayload(payload: Record<string, unknown>): string {
  const keys = Object.keys(payload);
  if (keys.length === 0) {
    return "No payload details";
  }
  const summaryKeys = keys.slice(0, 2);
  return summaryKeys
    .map((key) => `${key}: ${String(payload[key])}`)
    .join(" • ")
    .slice(0, 140);
}

export default function NotificationBell() {
  const { getToken } = useAuth();
  const containerRef = useRef<HTMLDivElement | null>(null);

  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [items, setItems] = useState<UserNotification[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadInitial = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const user = await getCurrentUser(getToken);
      setCurrentUser(user);
      const response = await getNotifications(getToken, { userId: user.id, cursor: 0, limit: 10 });
      setItems(response.items);
      setNextCursor(response.next_cursor);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Failed to load notifications";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current) {
        return;
      }
      if (event.target instanceof Node && !containerRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const unreadCount = useMemo(() => items.filter((item) => item.status !== "read").length, [items]);

  async function loadMore() {
    if (!currentUser || !nextCursor) {
      return;
    }
    setError(null);
    try {
      const response = await getNotifications(getToken, { userId: currentUser.id, cursor: nextCursor, limit: 10 });
      setItems((prev) => [...prev, ...response.items]);
      setNextCursor(response.next_cursor);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Failed to load more notifications";
      setError(message);
    }
  }

  async function handleMarkRead(notification: UserNotification) {
    if (!currentUser || notification.status === "read") {
      return;
    }
    try {
      const updated = await markNotificationRead(getToken, notification.id, currentUser.id);
      setItems((prev) => prev.map((item) => (item.id === notification.id ? updated : item)));
    } catch (markError) {
      const message = markError instanceof Error ? markError.message : "Failed to mark notification read";
      setError(message);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-label="Notifications"
        className="relative flex h-8 w-8 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-bg-subtle hover:text-text-primary"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unreadCount > 0 ? (
          <span
            className="absolute right-1 top-1 h-2 w-2 rounded-full"
            style={{ background: "var(--accent)" }}
          />
        ) : null}
      </button>

      {isOpen ? (
        <div className="absolute right-0 z-50 mt-2 w-96 max-w-[90vw] rounded-2xl border border-border bg-surface p-3 shadow-xl">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-text-primary">Notifications</p>
            <button
              type="button"
              onClick={() => void loadInitial()}
              className="rounded-full border border-border px-2.5 py-1 text-xs text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
            >
              Refresh
            </button>
          </div>

          {isLoading ? <p className="text-sm text-text-secondary">Loading...</p> : null}
          {error ? (
            <p className="mb-2 rounded-lg bg-danger-subtle px-3 py-2 text-xs font-medium text-danger">{error}</p>
          ) : null}

          {!isLoading && items.length === 0 ? (
            <p className="text-sm text-text-secondary">No notifications yet.</p>
          ) : null}

          <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => void handleMarkRead(item)}
                className={`w-full rounded-xl border p-3 text-left transition-colors ${
                  item.status === "read"
                    ? "border-border bg-bg-subtle"
                    : "border-border-strong bg-accent-subtle"
                }`}
              >
                <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
                  {item.event?.event_type ?? "unknown"}
                </p>
                <p className="mt-1 text-sm text-text-primary">
                  {item.event ? summarizePayload(item.event.payload) : "No event payload"}
                </p>
                <p className="mt-1 text-xs text-text-tertiary">
                  {item.read_at ? `Read ${item.read_at.replace("T", " ").slice(0, 19)}` : "Click to mark read"}
                </p>
              </button>
            ))}
          </div>

          {nextCursor ? (
            <div className="mt-3 border-t border-border pt-2">
              <button
                type="button"
                onClick={() => void loadMore()}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-secondary transition-colors hover:text-text-primary"
              >
                Load More
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
