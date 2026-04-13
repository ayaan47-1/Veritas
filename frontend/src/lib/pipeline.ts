import type { DocumentStatus } from "@/lib/types";

export const STAGE_PROGRESS: Record<string, number> = {
  uploaded: 5,
  parsing: 20,
  ocr: 30,
  chunking: 45,
  classification: 60,
  extraction: 72,
  verification: 84,
  critic_review: 88,
  scoring: 92,
  rescoring: 96,
  partially_processed: 100,
  complete: 100,
  failed: 100,
};

export function isTerminalParseStatus(parseStatus?: string | null): boolean {
  return parseStatus === "complete" || parseStatus === "partially_processed" || parseStatus === "failed";
}

export function isInProgressParseStatus(parseStatus?: string | null): boolean {
  return Boolean(parseStatus) && parseStatus !== "uploaded" && !isTerminalParseStatus(parseStatus);
}

export function computeProgressPercent(
  status: DocumentStatus | null | undefined,
  fallbackParseStatus?: string | null,
): number {
  const parseStatus = status?.parse_status ?? fallbackParseStatus ?? undefined;
  const stageProgress = parseStatus ? (STAGE_PROGRESS[parseStatus] ?? 0) : 0;
  const pageProgress =
    status && status.total_pages && status.total_pages > 0
      ? Math.round(((status.pages_processed + status.pages_failed) / status.total_pages) * 100)
      : 0;
  const rawProgress = Math.max(stageProgress, pageProgress);
  return isTerminalParseStatus(parseStatus) ? 100 : Math.min(rawProgress, 99);
}
