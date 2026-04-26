"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getCurrentUser, getDocumentStatus, ingestBulkDocuments } from "@/lib/api";
import type { CurrentUser, DocumentStatus } from "@/lib/types";

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const TERMINAL_STATUSES = new Set(["complete", "completed", "failed", "partially_processed"]);
const STAGE_PROGRESS: Record<string, number> = {
  uploaded: 5,
  queued: 5,
  parsing: 15,
  ocr: 25,
  chunking: 35,
  classification: 45,
  extracting: 60,
  extraction: 60,
  verifying: 80,
  verification: 80,
  critic_review: 85,
  scoring: 90,
  rescoring: 95,
  complete: 100,
  completed: 100,
  failed: 100,
  partially_processed: 100,
};

type UploadFile = {
  id: string;
  file: File;
  path: string;
};

type QueueRow = {
  filename: string;
  documentId: string | null;
  status: string;
  progress: number;
  error: string | null;
};

type FileSystemEntryLike = {
  isFile: boolean;
  isDirectory: boolean;
  fullPath: string;
};

type FileSystemFileEntryLike = FileSystemEntryLike & {
  file(callback: (file: File) => void): void;
};

type FileSystemDirectoryEntryLike = FileSystemEntryLike & {
  createReader(): {
    readEntries(callback: (entries: FileSystemEntryLike[]) => void): void;
  };
};

type DataTransferItemWithEntry = DataTransferItem & {
  webkitGetAsEntry?: () => FileSystemEntryLike | null;
};

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileKey(file: File, path: string) {
  return `${path}:${file.name}:${file.size}:${file.lastModified}`;
}

function queueStatus(rawStatus: string) {
  if (rawStatus === "uploaded") return "queued";
  if (rawStatus === "complete") return "completed";
  if (rawStatus === "extraction") return "extracting";
  if (rawStatus === "verification") return "verifying";
  return rawStatus;
}

function statusStyle(status: string) {
  if (status === "completed") {
    return { background: "var(--success-subtle)", color: "var(--success)", borderColor: "var(--success)" };
  }
  if (status === "failed") {
    return { background: "var(--danger-subtle)", color: "var(--danger)", borderColor: "var(--danger)" };
  }
  if (status === "partially_processed") {
    return { background: "var(--accent-subtle)", color: "var(--accent)", borderColor: "var(--accent)" };
  }
  return { background: "var(--info-subtle)", color: "var(--info)", borderColor: "var(--info)" };
}

async function readDirectory(entry: FileSystemDirectoryEntryLike): Promise<UploadFile[]> {
  const reader = entry.createReader();
  const entries = await new Promise<FileSystemEntryLike[]>((resolve) => reader.readEntries(resolve));
  const nested = await Promise.all(entries.map(readEntry));
  return nested.flat();
}

async function readEntry(entry: FileSystemEntryLike): Promise<UploadFile[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve) => (entry as FileSystemFileEntryLike).file(resolve));
    const path = entry.fullPath.replace(/^\//, "") || file.name;
    return [{ id: fileKey(file, path), file, path }];
  }
  if (entry.isDirectory) {
    return readDirectory(entry as FileSystemDirectoryEntryLike);
  }
  return [];
}

async function filesFromDrop(event: React.DragEvent<HTMLDivElement>): Promise<UploadFile[]> {
  const entries = Array.from(event.dataTransfer.items)
    .map((item): FileSystemEntryLike | null => {
      const entry = (item as DataTransferItemWithEntry).webkitGetAsEntry?.();
      return entry ? (entry as FileSystemEntryLike) : null;
    })
    .filter((entry): entry is FileSystemEntryLike => entry !== null);

  if (entries.length > 0) {
    const nested = await Promise.all(entries.map(readEntry));
    return nested.flat();
  }

  return Array.from(event.dataTransfer.files).map((file) => {
    const path = file.webkitRelativePath || file.name;
    return { id: fileKey(file, path), file, path };
  });
}

export default function BulkUploadPage() {
  const { getToken } = useAuth();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const assetId = useMemo(() => params.id, [params.id]);
  const pollCursorRef = useRef(0);

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getCurrentUser(getToken)
      .then(setUser)
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : "Failed to load user"));
  }, [getToken]);

  const addFiles = useCallback((incoming: UploadFile[]) => {
    const pdfs: UploadFile[] = [];
    const rejected: string[] = [];

    incoming.forEach((item) => {
      if (item.file.name.toLowerCase().endsWith(".pdf")) {
        pdfs.push(item);
      } else {
        rejected.push(item.path);
      }
    });

    setFiles((current) => {
      const byId = new Map(current.map((item) => [item.id, item]));
      pdfs.forEach((item) => byId.set(item.id, item));
      return Array.from(byId.values());
    });
    setSkipped((current) => [...current, ...rejected]);
  }, []);

  const totalSize = files.reduce((sum, item) => sum + item.file.size, 0);
  const oversizedFiles = files.filter((item) => item.file.size > MAX_FILE_SIZE);
  const terminalCount = queue.filter((row) => TERMINAL_STATUSES.has(row.status)).length;
  const failedCount = queue.filter((row) => row.status === "failed").length;
  const completedCount = queue.filter((row) => row.status === "completed").length;
  const inProgressCount = Math.max(queue.length - terminalCount, 0);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || files.length === 0) return;

    setIsUploading(true);
    setError(null);
    try {
      const response = await ingestBulkDocuments(getToken, {
        assetId,
        uploadedBy: user.id,
        files: files.map((item) => item.file),
      });
      const succeededRows = response.succeeded.map((item) => ({
        filename: item.filename,
        documentId: item.document_id,
        status: "queued",
        progress: STAGE_PROGRESS.queued,
        error: null,
      }));
      const failedRows = response.failed.map((item) => ({
        filename: item.filename,
        documentId: null,
        status: "failed",
        progress: 100,
        error: item.reason,
      }));
      setQueue([...succeededRows, ...failedRows]);
      setFiles([]);
      setSkipped([]);
      pollCursorRef.current = 0;
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Bulk upload failed");
    } finally {
      setIsUploading(false);
    }
  }

  useEffect(() => {
    if (queue.length === 0 || queue.every((row) => TERMINAL_STATUSES.has(row.status))) return;

    const poll = async () => {
      const candidates = queue.filter((row) => row.documentId && !TERMINAL_STATUSES.has(row.status));
      if (candidates.length === 0) return;

      const start = pollCursorRef.current % candidates.length;
      const batch = [...candidates.slice(start), ...candidates.slice(0, start)].slice(0, 10);
      pollCursorRef.current = (start + batch.length) % candidates.length;

      const results = await Promise.allSettled(
        batch.map(async (row) => {
          const status = await getDocumentStatus(getToken, row.documentId as string);
          return { documentId: row.documentId, status };
        }),
      );

      setQueue((current) =>
        current.map((row) => {
          const match = results.find(
            (result): result is PromiseFulfilledResult<{ documentId: string | null; status: DocumentStatus }> =>
              result.status === "fulfilled" && result.value.documentId === row.documentId,
          );
          if (!match) return row;
          const status = queueStatus(match.value.status.parse_status);
          return {
            ...row,
            status,
            progress: STAGE_PROGRESS[status] ?? row.progress,
            error: status === "failed" ? row.error ?? "Processing failed" : row.error,
          };
        }),
      );
    };

    void poll();
    const interval = window.setInterval(() => void poll(), 3000);
    return () => window.clearInterval(interval);
  }, [getToken, queue]);

  return (
    <main className="min-h-screen bg-bg px-6 py-10">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-2xl text-text-primary">Bulk Upload</h1>
            <p className="mt-1 text-sm text-text-secondary font-mono">{assetId}</p>
          </div>
          <div className="flex gap-2">
            <Link href={`/assets/${assetId}/documents`} className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary">
              Documents
            </Link>
            <Link href="/" className="rounded-full border border-border px-3 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary">
              Assets
            </Link>
          </div>
        </header>

        {queue.length === 0 ? (
          <form onSubmit={handleSubmit} className="space-y-5">
            <div
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setIsDragging(false);
                void filesFromDrop(event).then(addFiles);
              }}
              className={`flex min-h-72 flex-col items-center justify-center rounded-2xl border-2 border-dashed bg-surface px-6 py-10 text-center transition-colors ${
                isDragging ? "border-brand" : "border-border"
              }`}
            >
              <p className="font-serif text-xl text-text-primary">Drop PDFs or a folder here</p>
              <p className="mt-2 max-w-lg text-sm text-text-secondary">
                Select multiple PDFs or choose a folder. Non-PDF files are skipped before upload.
              </p>
              <div className="mt-6 flex flex-wrap justify-center gap-3">
                <label className="cursor-pointer rounded-full bg-brand px-4 py-2 text-sm font-medium text-bg">
                  Choose PDFs
                  <input
                    type="file"
                    accept=".pdf,application/pdf"
                    multiple
                    onChange={(event) => {
                      const selected = Array.from(event.target.files ?? []).map((file) => ({
                        id: fileKey(file, file.name),
                        file,
                        path: file.name,
                      }));
                      addFiles(selected);
                      event.target.value = "";
                    }}
                    className="sr-only"
                  />
                </label>
                <label className="cursor-pointer rounded-full border border-border px-4 py-2 text-sm font-medium text-text-primary">
                  Choose Folder
                  <input
                    type="file"
                    multiple
                    {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                    onChange={(event) => {
                      const selected = Array.from(event.target.files ?? []).map((file) => {
                        const path = file.webkitRelativePath || file.name;
                        return { id: fileKey(file, path), file, path };
                      });
                      addFiles(selected);
                      event.target.value = "";
                    }}
                    className="sr-only"
                  />
                </label>
              </div>
            </div>

            {error ? <p className="rounded-xl bg-danger-subtle px-4 py-3 text-sm font-medium text-danger">{error}</p> : null}

            {skipped.length > 0 ? (
              <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text-secondary">
                Skipped {skipped.length} non-PDF file{skipped.length === 1 ? "" : "s"}.
              </div>
            ) : null}

            {oversizedFiles.length > 0 ? (
              <div className="rounded-xl bg-accent-subtle px-4 py-3 text-sm font-medium text-accent">
                {oversizedFiles.length} file{oversizedFiles.length === 1 ? "" : "s"} exceed the 50MB guideline.
              </div>
            ) : null}

            <section className="rounded-2xl border border-border bg-surface shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
                <p className="text-sm font-medium text-text-primary">
                  {files.length} file{files.length === 1 ? "" : "s"} selected
                  <span className="ml-2 text-text-secondary">({formatBytes(totalSize)})</span>
                </p>
                <button
                  type="submit"
                  disabled={files.length === 0 || isUploading || !user}
                  className="rounded-full bg-brand px-4 py-2 text-sm font-medium text-bg disabled:opacity-50"
                >
                  {isUploading ? "Uploading..." : `Upload ${files.length} file${files.length === 1 ? "" : "s"}`}
                </button>
              </div>
              {files.length === 0 ? (
                <p className="px-4 py-8 text-sm text-text-secondary">No PDFs selected yet.</p>
              ) : (
                <ul className="divide-y divide-border">
                  {files.map((item) => (
                    <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                      <div>
                        <p className="text-sm font-medium text-text-primary">{item.file.name}</p>
                        <p className="mt-1 text-xs text-text-secondary">{item.path} - {formatBytes(item.file.size)}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setFiles((current) => current.filter((file) => file.id !== item.id))}
                        className="rounded-full border border-border px-3 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary"
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </form>
        ) : (
          <section className="rounded-2xl border border-border bg-surface shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-4">
              <p className="text-sm font-medium text-text-primary">
                {completedCount} of {queue.length} documents completed, {inProgressCount} in progress, {failedCount} failed
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setQueue([]);
                    setError(null);
                  }}
                  className="rounded-full border border-border px-3 py-1.5 text-sm font-medium text-text-primary"
                >
                  Add more files
                </button>
                <button
                  type="button"
                  onClick={() => router.push(`/assets/${assetId}/documents`)}
                  className="rounded-full bg-brand px-3 py-1.5 text-sm font-medium text-bg"
                >
                  Done - view documents
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-bg-subtle">
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Filename</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Progress</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-text-tertiary">Document</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.map((row) => (
                    <tr key={`${row.filename}:${row.documentId ?? row.error}`} className="border-t border-border">
                      <td className="px-4 py-3">
                        <p className="font-medium text-text-primary">{row.filename}</p>
                        {row.error ? <p className="mt-1 text-xs font-medium text-danger">{row.error}</p> : null}
                      </td>
                      <td className="px-4 py-3">
                        <span style={statusStyle(row.status)} className="inline-flex rounded-full border px-2.5 py-1 text-xs font-medium">
                          {row.status}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="h-2 w-48 overflow-hidden rounded-full bg-bg-subtle">
                          <div className="h-full bg-brand transition-all" style={{ width: `${row.progress}%` }} />
                        </div>
                        <p className="mt-1 text-xs text-text-secondary">{row.progress}%</p>
                      </td>
                      <td className="px-4 py-3">
                        {row.documentId ? (
                          <Link href={`/documents/${row.documentId}`} className="text-sm font-medium text-brand underline decoration-border underline-offset-4">
                            View
                          </Link>
                        ) : (
                          <span className="text-sm text-text-secondary">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
