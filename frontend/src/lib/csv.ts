/** CSV export utility — properly escapes fields and triggers browser download. */

function escapeField(value: string | number | null | undefined): string {
  if (value == null) return "";
  const str = String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

export function downloadCsv(
  filename: string,
  headers: string[],
  rows: (string | number | null | undefined)[][],
): void {
  const headerLine = headers.map(escapeField).join(",");
  const body = rows.map((row) => row.map(escapeField).join(",")).join("\n");
  const csv = `${headerLine}\n${body}`;

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function csvFilename(prefix: string, assetName: string): string {
  const date = new Date().toISOString().slice(0, 10);
  const safe = assetName.replace(/[^a-zA-Z0-9_-]/g, "_").replace(/_+/g, "_");
  return `VeritasLayer_${prefix}_${safe}_${date}.csv`;
}

export function parseFilenameFromDisposition(
  header: string | null,
  fallback: string = "export",
): string {
  if (!header) return fallback;
  const match = header.match(/filename="([^"]+)"/i);
  return match ? match[1] : fallback;
}

export async function downloadExport(
  endpoint: "obligations" | "risks",
  params: URLSearchParams,
  format: "csv" | "xlsx",
  token: string,
): Promise<void> {
  const query = new URLSearchParams(params);
  query.set("format", format);
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const res = await fetch(`${base}/exports/${endpoint}?${query.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    let detail = `Export failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // response wasn't JSON — keep default message
    }
    throw new Error(detail);
  }
  const filename = parseFilenameFromDisposition(
    res.headers.get("content-disposition"),
    `${endpoint}.${format}`,
  );
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
