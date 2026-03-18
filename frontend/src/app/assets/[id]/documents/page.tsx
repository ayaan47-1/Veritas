"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getAssetDocuments, getCurrentUser, ingestDocument } from "@/lib/api";
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

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">P1 Screen</p>
            <h1 className="text-2xl font-semibold text-slate-900">Asset Documents</h1>
            <p className="text-sm text-slate-600">Asset: {assetId}</p>
          </div>
          <div className="flex gap-2">
            <Link href="/" className="rounded-full border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700">
              Assets
            </Link>
            <Link
              href={`/obligations?asset_id=${assetId}`}
              className="rounded-full bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white"
            >
              Obligations
            </Link>
          </div>
        </header>

        <form onSubmit={handleUpload} className="mb-5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-sm font-semibold text-slate-900">Upload document</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <input
              type="file"
              accept=".pdf,.txt,application/pdf,text/plain"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              className="max-w-md rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800"
            />
            <button
              type="submit"
              disabled={isUploading}
              className="rounded-full bg-cyan-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {isUploading ? "Uploading..." : "Upload"}
            </button>
          </div>
        </form>

        <div className="mb-4 flex flex-wrap gap-3">
          <label className="text-sm">
            <span className="mr-2 font-semibold text-slate-700">Doc type</span>
            <select
              value={docType}
              onChange={(event) => setDocType(event.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm"
            >
              {DOC_TYPES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            <span className="mr-2 font-semibold text-slate-700">Parse status</span>
            <select
              value={parseStatus}
              onChange={(event) => setParseStatus(event.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm"
            >
              {PARSE_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>

        {isLoading ? <p className="text-sm text-slate-600">Loading documents...</p> : null}
        {error ? <p className="mb-4 rounded-xl bg-rose-100 px-4 py-3 text-sm font-medium text-rose-700">{error}</p> : null}

        {!isLoading && !error ? (
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full border-collapse text-sm">
              <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-200">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Doc Type</th>
                  <th className="px-4 py-3">Parse Status</th>
                  <th className="px-4 py-3">Uploaded At</th>
                  <th className="px-4 py-3">Pages</th>
                </tr>
              </thead>
              <tbody>
                {items.map((document) => (
                  <tr key={document.id} className="border-t border-slate-100">
                    <td className="px-4 py-3 font-medium text-slate-900">{document.source_name}</td>
                    <td className="px-4 py-3 text-slate-600">{document.doc_type}</td>
                    <td className="px-4 py-3 text-slate-600">{document.parse_status}</td>
                    <td className="px-4 py-3 text-slate-600">
                      {document.uploaded_at ? document.uploaded_at.replace("T", " ").slice(0, 19) : "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{document.total_pages ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {nextCursor ? (
              <div className="border-t border-slate-100 p-3">
                <button
                  onClick={() => void loadPage(nextCursor, true)}
                  className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
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
