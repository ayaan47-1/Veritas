import { describe, expect, it } from "vitest";
import { parseFilenameFromDisposition } from "../csv";

describe("parseFilenameFromDisposition", () => {
  it("extracts quoted filename", () => {
    expect(
      parseFilenameFromDisposition('attachment; filename="obligations_willow_2026-04-23.csv"'),
    ).toBe("obligations_willow_2026-04-23.csv");
  });

  it("returns fallback when header missing", () => {
    expect(parseFilenameFromDisposition(null, "fallback.csv")).toBe("fallback.csv");
  });

  it("returns fallback when no filename in header", () => {
    expect(parseFilenameFromDisposition("attachment", "fallback.csv")).toBe("fallback.csv");
  });
});
