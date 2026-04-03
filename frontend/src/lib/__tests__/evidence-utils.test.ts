import { describe, it, expect } from "vitest";
import {
  summarizeText,
  buildContextBlocks,
  isRedundantWithQuote,
  buildContextDigest,
  formatQuoteAsProse,
} from "../evidence-utils";

describe("summarizeText", () => {
  it("returns text unchanged when within maxLength", () => {
    expect(summarizeText("Short text.", 120)).toBe("Short text.");
  });

  it("returns text unchanged when exactly at maxLength", () => {
    const text = "a".repeat(120);
    expect(summarizeText(text, 120)).toBe(text);
  });

  it("truncates at sentence boundary when one exists before maxLength", () => {
    const text = "First sentence. Second sentence that is quite long and continues past the limit.";
    const result = summarizeText(text, 40);
    expect(result).toBe("First sentence.");
  });

  it("truncates at word boundary with ellipsis when no sentence break before limit", () => {
    const text = "This is a single very long sentence that will be truncated at a word boundary somewhere";
    const result = summarizeText(text, 30);
    expect(result.endsWith("…")).toBe(true);
    expect(result.length).toBeLessThanOrEqual(31);
    const withoutEllipsis = result.slice(0, -1);
    expect(text.startsWith(withoutEllipsis.trim())).toBe(true);
  });

  it("normalizes extra whitespace", () => {
    expect(summarizeText("  hello   world  ", 120)).toBe("hello world");
  });

  it("uses default maxLength of 120", () => {
    const text = "a".repeat(200);
    const result = summarizeText(text);
    expect(result.length).toBeLessThanOrEqual(121); // 120 chars + "…"
  });
});

describe("buildContextBlocks", () => {
  it("returns empty array for empty text", () => {
    expect(buildContextBlocks("")).toEqual([]);
  });

  it("splits bullet lines into separate blocks", () => {
    const text = "• Contractor shall deliver all materials on time\n• Owner must approve all change orders\n• Subcontractors require written consent";
    const blocks = buildContextBlocks(text);
    expect(blocks).toContain("Contractor shall deliver all materials on time");
    expect(blocks).toContain("Owner must approve all change orders");
  });

  it("merges non-bullet continuation lines", () => {
    const text = "• First item\ncontinuation of first";
    const blocks = buildContextBlocks(text);
    expect(blocks[0]).toContain("continuation");
  });

  it("filters out very short blocks (fewer than 12 chars)", () => {
    const text = "• Hi\n• This is a longer valid line for testing purposes";
    const blocks = buildContextBlocks(text);
    expect(blocks.every((b) => b.length > 12)).toBe(true);
  });
});

describe("isRedundantWithQuote", () => {
  it("returns true for identical text", () => {
    expect(isRedundantWithQuote("the contractor shall deliver", "the contractor shall deliver")).toBe(true);
  });

  it("returns true when context contains the quote", () => {
    expect(isRedundantWithQuote("the contractor shall deliver by december", "shall deliver")).toBe(true);
  });

  it("returns true when quote contains the context", () => {
    expect(isRedundantWithQuote("deliver", "the contractor shall deliver by december")).toBe(true);
  });

  it("returns true for high Jaccard similarity (≥0.8)", () => {
    expect(isRedundantWithQuote("contractor shall deliver by december", "contractor shall deliver by december 31")).toBe(true);
  });

  it("returns false for unrelated text", () => {
    expect(isRedundantWithQuote("payment terms and conditions apply", "contractor shall deliver by december")).toBe(false);
  });

  it("returns false for empty strings", () => {
    expect(isRedundantWithQuote("", "some quote")).toBe(false);
    expect(isRedundantWithQuote("some context", "")).toBe(false);
  });
});

describe("buildContextDigest", () => {
  it("returns empty bullets for empty raw text", () => {
    const result = buildContextDigest("", 0, 10, "some quote");
    expect(result.bullets).toEqual([]);
  });

  it("extracts references from quote text", () => {
    const result = buildContextDigest("", 0, 10, "payment due within 30 days per Section 4.2");
    expect(result.references).toContain("30 days");
  });
});

describe("formatQuoteAsProse", () => {
  it("returns single string for plain quote", () => {
    const result = formatQuoteAsProse("The contractor shall deliver by December 31.");
    expect(result).toEqual({ type: "paragraph", text: "The contractor shall deliver by December 31." });
  });

  it("returns bullet list when quote contains bullet markers", () => {
    const result = formatQuoteAsProse("• First requirement\n• Second requirement\n• Third requirement");
    expect(result.type).toBe("bullets");
    if (result.type === "bullets") {
      expect(result.items.length).toBeGreaterThanOrEqual(2);
    }
  });

  it("returns bullets when quote has numbered list items", () => {
    const result = formatQuoteAsProse("1. First item\n2. Second item\n3. Third item");
    expect(result.type).toBe("bullets");
  });

  it("returns paragraph for normal prose text", () => {
    const result = formatQuoteAsProse("The owner shall provide access to the premises during normal business hours.");
    expect(result.type).toBe("paragraph");
  });
});
