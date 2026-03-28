"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteDocument, getAssetDocuments, getCurrentUser, ingestDocument } from "@/lib/api";
import type { CurrentUser, DocumentSummary } from "@/lib/types";

const DOC_TYPES = ["all", "contract", "invoice", "inspection_report", "rfi", "change_order", "unknown"] as const;
const PARSE_STATUSES = ["all", "uploaded", "parsing", "ocr", "chunking", "classification", "extraction", "verification", "scoring", "complete", "partially_processed", "failed"] as const;

export default function AssetDocumentsPage() {
  const { getToken } = useAuth();
  const params = useParams<{ id: string }>();
  const assetId = useMemo(() => params.id, [params.id]);

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [docType, setDocType] = useState<string>("all");
  const [parseStatus, setParseStatus] = useState<string>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const loadPage = useCallback(
    async (cursor: string | number, append: boolean) => {
      setError(null);
      try {
        const [currentUser, response] = await Promise.all([
          getCurrentUser(getToken),
          getAssetDocuments(getToken, {
            assetId,
            docType: docType === "all" ? undefined : docType,
            parseStatus: parseStatus === "all" ? undefined : parseStatus,
            limit: 20,
            cursor,
          }),
        ]);
        setUser(currentUser);
        setItems((prev) => (append ? [...prev, ...response.items] : response.items));
        setNextCursor(response.next_cursor);
      } catch (loadError) {
        const message = loadError instanceof Error ? loadError.message : "Failed to load documents";
        setError(message);
      } finally {
        setIsLoading(false);
      }
    },
    [assetId, docType, parseStatus, getToken],
  );

  useEffect(() => {
    setIsLoading(true);
    setItems([]);
    setNextCursor(null);
    void loadPage(0, false);
  }, [loadPage]);

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) {
      setError("Choose a file first.");
      return;
    }
    if (!user) {
      setError("Current user not loaded yet.");
      return;
    }
    setIsUploading(true);
    setError(null);
    try {
      await ingestDocument(getToken, {
        assetId,
        uploadedBy: user.id,
        file: selectedFile,
      });
      setSelectedFile(null);
      await loadPage(0, false);
    } catch (uploadError) {
      const message = uploadError instanceof Error ? uploadError.message : "Upload failed";
      setError(message);
    } finally {
      setIsUploading(false);
    }
  }

  async function handleDelete(documentId: string) {
    if (!confirm("Delete this document and all its extracted data?")) return;
    setDeletingId(documentId);
    try {
      await deleteDocument(getToken, documentId);
      setItems((prev) => prev.filter((d) => d.id !== documentId));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-2xl text-text-primary">Asset Documents</h1>
            <p className="mt-1 text-sm text-text-secondary font-mono">{assetId}</p>
          </div>
          <div className="flex gap-2">
            <Link href="/" className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary">
              Assets
            </Link>
            <Link
              href={`/obligations?asset_id=${assetId}`}
              className="rounded-full bg-brand px-3 py-1.5 text-sm font-medium text-bg"
            >
              Obligations
            </Link>
          </div>
        </header>

        <form onSubmit={handleUpload} className="mb-6 rounded-2xl border border-border bg-surface p-5 shadow-sm">
          <p className="text-sm font-medium text-text-primary">Upload document</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <input
              type="file"
              accept=".pdf,.txt,application/pdf,text/plain"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              className="max-w-md rounded-xl border border-border bg-bg-subtle px-3 py-2 text-sm text-text-primary"
            />
            <button
              type="submit"
              disabled={isUploading}
              className="rounded-full bg-brand px-4 py-2 text-sm font-medium text-bg disabled:opacity-50"
            >
              {isUploading ? "Uploading..." : "Upload"}
            </button>
          </div>
        </form>

        <div className="mb-5 flex flex-wrap gap-3">
          <label className="text-sm">
            <span className="mr-2 font-medium text-text-secondary">Doc type</span>
            <select
              value={docType}
              onChange={(event) => setDocType(event.target.value)}
              className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-text-primary outline-none focus:border-border-strong"
            >
              {DOC_TYPES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            <span className="mr-2 font-medium text-text-secondary">Parse status</span>
            <select
              value={parseStatus}
              onChange={(event) => setParseStatus(event.target.value)}
              className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-text-primary outline-none focus:border-border-strong"
            >
              {PARSE_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>

        {isLoading ? <p className="text-sm text-text-secondary">Loading documents...</p> : null}
        {error ? (
          <p className="mb-4 rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p>
        ) : null}

        {!isLoading && !error ? (
          <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-subtle">
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Doc Type</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Parse Status</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Uploaded At</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Pages</th>
                  <th className="px-4 py-3"></th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((document) => (
                  <tr key={document.id} className="border-t border-border transition-colors hover:bg-bg-subtle">
                    <td className="px-4 py-3 font-medium text-text-primary">
                      <Link href={`/documents/${document.id}`} className="underline decoration-border underline-offset-4 hover:decoration-border-strong">
                        {document.source_name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-text-secondary">{document.doc_type}</td>
                    <td className="px-4 py-3 text-text-secondary">{document.parse_status}</td>
                    <td className="px-4 py-3 text-text-secondary">
                      {document.uploaded_at ? document.uploaded_at.replace("T", " ").slice(0, 19) : "—"}
                    </td>
                    <td className="px-4 py-3 text-text-secondary">{document.total_pages ?? "—"}</td>
                    <td className="px-4 py-3">
                      <Link
                        href={`/documents/${document.id}`}
                        style={{ background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" }}
                        className="rounded-full border px-2.5 py-1 text-xs font-medium"
                      >
                        View
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => void handleDelete(document.id)}
                        disabled={deletingId === document.id}
                        style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                        className="rounded-full border px-2.5 py-1 text-xs font-medium disabled:opacity-50"
                      >
                        {deletingId === document.id ? "Deleting..." : "Delete"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {nextCursor ? (
              <div className="border-t border-border p-3">
                <button
                  onClick={() => void loadPage(nextCursor, true)}
                  className="rounded-full border border-border px-3 py-1.5 text-xs text-text-secondary transition-colors hover:text-text-primary"
                >
                  Load More
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </main>
  );
}
