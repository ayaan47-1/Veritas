/**
 * Pure utility functions for evidence display on obligation/risk detail pages.
 * Extracted here so they can be tested independently and shared across pages.
 */

export type QuoteFormat =
  | { type: "paragraph"; text: string }
  | { type: "bullets"; items: string[] };

export type ContextDigest = {
  bullets: string[];
  references: string[];
};

// ---------------------------------------------------------------------------
// Text summarization (for list pages)
// ---------------------------------------------------------------------------

/**
 * Returns a readable summary of text suitable for a table cell.
 * Prefers truncating at a sentence boundary; falls back to word boundary.
 */
export function summarizeText(text: string, maxLength = 120): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;

  // Try sentence boundary before the limit
  const sentenceEnd = normalized.indexOf(". ", 0);
  if (sentenceEnd > 0 && sentenceEnd < maxLength) {
    return normalized.slice(0, sentenceEnd + 1);
  }

  // Word-boundary truncation
  const truncated = normalized.slice(0, maxLength);
  const lastSpace = truncated.lastIndexOf(" ");
  return (lastSpace > 0 ? truncated.slice(0, lastSpace) : truncated) + "\u2026";
}

// ---------------------------------------------------------------------------
// Quote formatting (for evidence detail pages)
// ---------------------------------------------------------------------------

const BULLET_RE = /^[\u2022\-\u2013\*]\s*/;
const NUMBERED_RE = /^\d+[.)]\s+/;

function isBulletLine(line: string): boolean {
  return BULLET_RE.test(line) || NUMBERED_RE.test(line);
}

/**
 * Decides whether a quote should be rendered as a paragraph or a bullet list,
 * and returns the appropriately structured data.
 */
export function formatQuoteAsProse(quote: string): QuoteFormat {
  const lines = quote
    .replace(/\r/g, "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  const bulletLines = lines.filter(isBulletLine);

  if (bulletLines.length >= 2) {
    const items = lines
      .filter(isBulletLine)
      .map((l) => l.replace(BULLET_RE, "").replace(NUMBERED_RE, "").trim())
      .filter(Boolean);
    return { type: "bullets", items };
  }

  return { type: "paragraph", text: quote.replace(/\s+/g, " ").trim() };
}

// ---------------------------------------------------------------------------
// Context digest (surrounding text analysis)
// ---------------------------------------------------------------------------

function normalizeInlineText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function canonicalizeText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function jaccardSimilarity(a: string, b: string): number {
  const aTokens = new Set(canonicalizeText(a).split(" ").filter(Boolean));
  const bTokens = new Set(canonicalizeText(b).split(" ").filter(Boolean));
  if (aTokens.size === 0 || bTokens.size === 0) return 0;
  let intersection = 0;
  for (const token of aTokens) {
    if (bTokens.has(token)) intersection += 1;
  }
  const union = aTokens.size + bTokens.size - intersection;
  return union > 0 ? intersection / union : 0;
}

export function isRedundantWithQuote(contextText: string, quote: string): boolean {
  const canonicalContext = canonicalizeText(contextText);
  const canonicalQuote = canonicalizeText(quote);
  if (!canonicalContext || !canonicalQuote) return false;
  if (canonicalContext === canonicalQuote) return true;
  if (canonicalContext.includes(canonicalQuote) || canonicalQuote.includes(canonicalContext)) return true;
  return jaccardSimilarity(canonicalContext, canonicalQuote) >= 0.8;
}

function isUsefulContextBullet(text: string): boolean {
  const normalized = normalizeInlineText(text);
  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length < 5) return false;
  if (/[(-]$/.test(normalized)) return false;
  if (/^\(?eff\.\s*\d/i.test(normalized) && words.length < 7) return false;
  return true;
}

export function buildContextBlocks(rawText: string): string[] {
  const lines = rawText
    .replace(/\r/g, "")
    .replace(/\u2022/g, "\n• ")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const blocks: Array<{ text: string; isBullet: boolean }> = [];

  for (const line of lines) {
    const isBullet = /^[•\-\u2013]/.test(line);
    const cleaned = normalizeInlineText(line.replace(/^[•\-\u2013]+\s*/, ""));
    if (!cleaned) continue;

    if (isBullet || blocks.length === 0) {
      blocks.push({ text: cleaned, isBullet });
      continue;
    }

    const last = blocks[blocks.length - 1];
    last.text = normalizeInlineText(`${last.text} ${cleaned}`);
  }

  return blocks
    .filter((block, index) => block.isBullet || index > 0)
    .map((block) => block.text)
    .filter((block) => block.length > 12);
}

function extractContextReferences(parts: string[]): string[] {
  const matches = parts.flatMap((part) =>
    Array.from(
      part.matchAll(
        /(?:§+\s*\d+(?:\.\d+)*)|(?:section|sec\.|clause|article|paragraph)\s+[A-Za-z0-9.-]+|\b\d+(?:\.\d+)+\b|\$\s*[\d,]+(?:\.\d+)?|\b\d+\s*(?:days?|weeks?|months?|years?)\b|\b\d+(?:\.\d+)?%/gi,
      ),
      (match) => normalizeInlineText(match[0]),
    ),
  );
  return Array.from(new Set(matches));
}

export function buildContextDigest(
  rawText: string,
  _start: number,
  _end: number,
  quote: string,
): ContextDigest {
  const normalizedQuote = normalizeInlineText(quote);
  if (!rawText.trim()) {
    return {
      bullets: [],
      references: normalizedQuote ? extractContextReferences([normalizedQuote]) : [],
    };
  }

  const blocks = buildContextBlocks(rawText);
  if (blocks.length === 0) {
    return {
      bullets: [],
      references: normalizedQuote ? extractContextReferences([normalizedQuote]) : [],
    };
  }

  const centerIndex = blocks.findIndex((block) => block.includes(normalizedQuote));
  if (centerIndex < 0) {
    return {
      bullets: [],
      references: normalizedQuote ? extractContextReferences([normalizedQuote]) : [],
    };
  }

  const bullets: string[] = [];
  const previous = blocks[centerIndex - 1];
  const next = blocks[centerIndex + 1];

  if (previous && !isRedundantWithQuote(previous, normalizedQuote) && isUsefulContextBullet(previous)) {
    bullets.push(previous);
  }
  if (next && !isRedundantWithQuote(next, normalizedQuote) && isUsefulContextBullet(next)) {
    bullets.push(next);
  }

  const references = extractContextReferences(bullets);

  return {
    bullets: Array.from(new Set(bullets)),
    references,
  };
}
