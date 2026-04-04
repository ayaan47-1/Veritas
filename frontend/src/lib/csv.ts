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
