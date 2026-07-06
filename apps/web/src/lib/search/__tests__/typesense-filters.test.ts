import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  POSTING_BASE_FILTER,
  POSTING_FLOW_FILTER,
  buildFilterString,
} from "../typesense-filters";

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

// =====================================================================
// Issue #2965: year-count queries measure FLOW (postings first seen in a
// time window, regardless of current is_active state), not snapshot.
// POSTING_FLOW_FILTER drops `is_active:true` so delisted-but-still-recent
// postings count toward the "in the last year" total.
// =====================================================================

describe("POSTING_FLOW_FILTER (#2965)", () => {
  it("does NOT include is_active — flow queries must include delisted postings", () => {
    expect(POSTING_FLOW_FILTER).not.toContain("is_active");
  });

  it("retains has_content:!=false (don't surface broken postings)", () => {
    expect(POSTING_FLOW_FILTER).toContain("has_content:!=false");
  });

  it("is exactly the content-quality clause", () => {
    expect(POSTING_FLOW_FILTER).toBe("has_content:!=false");
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
// Issue #2983: work-mode (location_types) filter clause.
// =====================================================================

describe("buildFilterString — workMode (#2983)", () => {
  it("emits the location_types clause when a single mode is set", () => {
    expect(buildFilterString({ workMode: ["remote"] })).toBe(
      "location_types:[remote]",
    );
  });

  it("emits a comma-separated list for multiple modes (Typesense OR)", () => {
    expect(buildFilterString({ workMode: ["remote", "hybrid"] })).toBe(
      "location_types:[remote,hybrid]",
    );
  });

  it("preserves the order the caller passed in (no auto-sort)", () => {
    expect(buildFilterString({ workMode: ["onsite", "remote", "hybrid"] })).toBe(
      "location_types:[onsite,remote,hybrid]",
    );
  });

  it("composes with other filters via &&", () => {
    const out = buildFilterString({
      locationIds: [101],
      workMode: ["remote"],
    });
    expect(out).toBe("location_ids:[101] && location_types:[remote]");
  });

  it("does NOT emit anything when workMode is undefined / empty", () => {
    expect(buildFilterString({ workMode: undefined })).toBe("");
    expect(buildFilterString({ workMode: [] })).toBe("");
  });

  it("drops unknown workMode tokens before interpolation", () => {
    expect(buildFilterString({ workMode: ["remote", "x) || is_active:true"] })).toBe(
      "location_types:[remote]",
    );
  });
});

describe("buildFilterString — input hardening", () => {
  it("drops non-numeric array entries instead of interpolating them", () => {
    const out = buildFilterString({
      locationIds: [101, "102) || is_active:true"],
      technologyIds: [10, Number.NaN, -1, 11],
    });
    expect(out).toBe("location_ids:[101] && technology_ids:[10,11]");
  });

  it("drops invalid token-list values", () => {
    const out = buildFilterString({
      employmentTypes: ["full-time", "contract] || is_active:true"],
      languages: ["en", "fr,remote"],
    });
    expect(out).toBe("employment_type:[full-time] && locales:[en,_none]");
  });

  it("does not emit NaN numeric ranges", () => {
    expect(
      buildFilterString({
        salaryMinEur: Number.NaN,
        salaryMaxEur: Number.NaN,
        experienceMin: Number.NaN,
        experienceMax: Number.NaN,
      }),
    ).toBe("");
  });
});

// =====================================================================
// Issue #2965: every `first_seen_at:>` (year-count) filter string in the
// Typesense providers must compose with POSTING_FLOW_FILTER, NOT
// POSTING_BASE_FILTER. Including `is_active:true` collapses year-count
// to active-count (an active job, by definition, was first-seen in the
// past — so the time window selects no extra docs once is_active is on).
//
// Also (#3029): apply the same invariant to `services/watchlists.ts`,
// where the watchlist-page year-count was constructed without any
// content-quality clause, inflating the year badge vs the active badge.
// =====================================================================

describe("year-count badge filters reference POSTING_FLOW_FILTER (#2965, #3029)", () => {
  const SOURCES = [
    "../typesense.ts",
    "../typesense-browser.ts",
    "../../services/watchlists.ts",
  ] as const;

  for (const rel of SOURCES) {
    it(`${rel}: every \`first_seen_at:>\` filter string mentions POSTING_FLOW_FILTER`, () => {
      const path = join(__dirname, rel);
      const src = readFileSync(path, "utf8");
      const lines = src.split("\n");

      // Match template-literal substrings starting with `first_seen_at:>`
      // and ending at the closing backtick on the same line. The five call
      // sites (#2965 + #3029) all build the year filter on a single line.
      const yearFilterLines = lines.filter((line) =>
        line.includes("first_seen_at:>") &&
        // ignore comment-only lines so we don't false-positive on docs
        !/^\s*\/\//.test(line),
      );

      expect(yearFilterLines.length).toBeGreaterThan(0);

      for (const line of yearFilterLines) {
        expect(
          line.includes("POSTING_FLOW_FILTER"),
          `expected POSTING_FLOW_FILTER in line:\n  ${line.trim()}`,
        ).toBe(true);
        expect(
          line.includes("POSTING_BASE_FILTER"),
          `year-count line must NOT use POSTING_BASE_FILTER (it inflates to is_active filter):\n  ${line.trim()}`,
        ).toBe(false);
      }
    });
  }
});
