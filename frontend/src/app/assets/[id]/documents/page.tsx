"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteDocument, getAssetDocuments, getCurrentUser, ingestDocument, processDocument } from "@/lib/api";
import type { CurrentUser, DocumentSummary } from "@/lib/types";

const DOC_TYPES = [
  "all",
  "contract",
  "lease",
  "invoice",
  "inspection_report",
  "rfi",
  "change_order",
  "purchase_agreement",
  "title_commitment",
  "hoa_document",
  "disclosure_report",
  "insurance_policy",
  "loan_agreement",
  "deed_of_trust",
  "unknown",
] as const;
const PARSE_STATUSES = ["all", "uploaded", "parsing", "ocr", "chunking", "classification", "extraction", "verification", "scoring", "complete", "partially_processed", "failed"] as const;

function domainBadgeStyle(domain: string | null) {
  if (domain === "real_estate") {
    return { background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" };
  }
  if (domain === "financial") {
    return { background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" };
  }
  if (domain === "construction") {
    return { background: "var(--bg-subtle)", color: "var(--text-secondary)", borderColor: "var(--border-strong)" };
  }
  return { background: "var(--bg-subtle)", color: "var(--text-secondary)", borderColor: "var(--border)" };
}

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
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [lastUploaded, setLastUploaded] = useState<{ id: string; name: string } | null>(null);

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
    setUploadError(null);
    try {
      const uploaded = await ingestDocument(getToken, {
        assetId,
        uploadedBy: user.id,
        file: selectedFile,
        autoProcess: false,
      });
      setLastUploaded({ id: uploaded.document_id, name: selectedFile.name });
      setSelectedFile(null);
      await loadPage(0, false);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setUploadError(message);
    } finally {
      setIsUploading(false);
    }
  }

  async function handleProcess(documentId: string) {
    setProcessingId(documentId);
    setError(null);
    try {
      await processDocument(getToken, documentId);
      setItems((prev) => prev.map((doc) => (doc.id === documentId ? { ...doc, parse_status: "parsing" } : doc)));
      setLastUploaded((prev) => (prev?.id === documentId ? null : prev));
    } catch (processError) {
      setError(processError instanceof Error ? processError.message : "Failed to queue document for processing");
    } finally {
      setProcessingId(null);
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
          {uploadError ? (
            <p className="mt-3 rounded-xl bg-danger-subtle px-3 py-2 text-sm font-medium text-danger">{uploadError}</p>
          ) : null}
          {lastUploaded ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-bg-subtle px-3 py-2">
              <p className="text-xs text-text-secondary">
                Uploaded <span className="font-medium text-text-primary">{lastUploaded.name}</span>. Click process to start the run.
              </p>
              <button
                type="button"
                onClick={() => void handleProcess(lastUploaded.id)}
                disabled={processingId === lastUploaded.id}
                style={{ background: "var(--accent-subtle)", color: "var(--accent)", borderColor: "var(--accent)" }}
                className="rounded-full border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
              >
                {processingId === lastUploaded.id ? "Processing..." : "Process Document"}
              </button>
            </div>
          ) : null}
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
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Domain</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Parse Status</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Uploaded At</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Pages</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Actions</th>
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
                    <td className="px-4 py-3">
                      <span
                        style={domainBadgeStyle(document.domain)}
                        className="inline-flex rounded-full border px-2.5 py-1 text-xs font-medium"
                      >
                        {document.domain ?? "—"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-text-secondary">{document.parse_status}</td>
                    <td className="px-4 py-3 text-text-secondary">
                      {document.uploaded_at ? document.uploaded_at.replace("T", " ").slice(0, 19) : "—"}
                    </td>
                    <td className="px-4 py-3 text-text-secondary">{document.total_pages ?? "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link
                          href={`/documents/${document.id}`}
                          style={{ background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" }}
                          className="rounded-full border px-2.5 py-1 text-xs font-medium"
                        >
                          View
                        </Link>
                        {document.parse_status === "uploaded" ? (
                          <button
                            onClick={() => void handleProcess(document.id)}
                            disabled={processingId === document.id}
                            style={{ background: "var(--accent-subtle)", color: "var(--accent)", borderColor: "var(--accent)" }}
                            className="rounded-full border px-2.5 py-1 text-xs font-medium disabled:opacity-50"
                          >
                            {processingId === document.id ? "Processing..." : "Process Document"}
                          </button>
                        ) : null}
                        <button
                          onClick={() => void handleDelete(document.id)}
                          disabled={deletingId === document.id}
                          style={{ background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" }}
                          className="rounded-full border px-2.5 py-1 text-xs font-medium disabled:opacity-50"
                        >
                          {deletingId === document.id ? "Deleting..." : "Delete"}
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
