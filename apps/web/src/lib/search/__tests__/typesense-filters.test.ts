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
