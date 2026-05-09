import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { POSTING_BASE_FILTER, buildFilterString } from "../typesense-filters";

describe("POSTING_BASE_FILTER", () => {
  it("includes is_active:true", () => {
    expect(POSTING_BASE_FILTER).toContain("is_active:true");
  });

  it("includes has_content:!=false (issue #2917)", () => {
    // `:!=false` (rather than `:true`) so docs not yet backfilled with
    // the new flag stay visible — only docs explicitly stamped `false`
    // are excluded. See typesense-filters.ts for full rationale.
    expect(POSTING_BASE_FILTER).toContain("has_content:!=false");
  });

  it("composes the two clauses with `&&`", () => {
    expect(POSTING_BASE_FILTER).toBe("is_active:true && has_content:!=false");
  });
});

describe("buildFilterString", () => {
  it("returns empty when no filters are set", () => {
    expect(buildFilterString({})).toBe("");
    expect(buildFilterString(undefined)).toBe("");
  });

  it("does NOT inject the base filter (callers prepend it)", () => {
    // Issue #2917: the base filter is intentionally excluded from
    // buildFilterString output so callers compose it explicitly. This
    // keeps `(filterStr ? " && " + filterStr : "")` patterns working
    // when the user has no extra filters.
    const out = buildFilterString({ locationIds: [101] });
    expect(out).not.toContain("is_active");
    expect(out).not.toContain("has_content");
    expect(out).toBe("location_ids:[101]");
  });
});

// =====================================================================
// Issue #2926: every `first_seen_at:>` (year-count) filter string in the
// Typesense providers must compose with POSTING_BASE_FILTER. Skipping it
// inflates yearly-posting badges to include incomplete postings.
// =====================================================================

describe("year-count badge filters reference POSTING_BASE_FILTER (#2926)", () => {
  const SOURCES = [
    "../typesense.ts",
    "../typesense-browser.ts",
  ] as const;

  for (const rel of SOURCES) {
    it(`${rel}: every \`first_seen_at:>\` filter string mentions POSTING_BASE_FILTER`, () => {
      const path = join(__dirname, rel);
      const src = readFileSync(path, "utf8");
      const lines = src.split("\n");

      // Match template-literal substrings starting with `first_seen_at:>`
      // and ending at the closing backtick on the same line. The four call
      // sites in #2926 all build the year filter on a single line.
      const yearFilterLines = lines.filter((line) =>
        line.includes("first_seen_at:>") &&
        // ignore comment-only lines so we don't false-positive on docs
        !/^\s*\/\//.test(line),
      );

      expect(yearFilterLines.length).toBeGreaterThan(0);

      for (const line of yearFilterLines) {
        expect(
          line.includes("POSTING_BASE_FILTER"),
          `expected POSTING_BASE_FILTER in line:\n  ${line.trim()}`,
        ).toBe(true);
      }
    });
  }
});
