import { describe, expect, it } from "vitest";
import {
  buildCacheKey,
  shouldRestoreSnapshot,
  type SearchStateSnapshot,
} from "@/components/SearchStateProvider";
import type { SearchResultCompany } from "@/lib/search";

/**
 * Regression tests for #2989 — empty-results race / snapshot poisoning.
 *
 * Before #2989, the predicate read:
 *   shouldRestore = cached !== null && (cached.cacheKey === currentCacheKey || !hasUrlFilters)
 *
 * The ``|| !hasUrlFilters`` branch let a previous filtered search's
 * snapshot — including ``companies: []`` from a 0-result keyword query
 * — leak into a fresh ``/explore`` visit (no URL filters). The user
 * then saw ``ZeroResults`` for whatever stale keywords lived in the
 * snapshot, even though the URL was clean.
 *
 * The strict cache-key match below ensures restoration only happens
 * when the current URL filters match the snapshot's filters exactly.
 */

function makeSnapshot(
  overrides: Partial<SearchStateSnapshot> = {},
): SearchStateSnapshot {
  return {
    keywords: [],
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
    workMode: [],
    salaryMinEur: undefined,
    salaryMaxEur: undefined,
    salaryCurrency: "EUR",
    experienceMin: undefined,
    experienceMax: undefined,
    companies: [] as SearchResultCompany[],
    totalCompanies: 0,
    showPostingId: null,
    scrollY: 0,
    cacheKey: buildCacheKey([], [], [], [], []),
    ...overrides,
  };
}

describe("buildCacheKey", () => {
  it("renders the empty-filter case as ||||", () => {
    expect(buildCacheKey([], [], [], [], [])).toBe("||||");
  });

  it("is stable under input ordering", () => {
    expect(buildCacheKey(["a", "b"], [2, 1], [10, 5], [], [])).toBe(
      buildCacheKey(["b", "a"], [1, 2], [5, 10], [], []),
    );
  });

  it("differentiates filtered vs unfiltered", () => {
    expect(buildCacheKey(["python"], [], [], [], [])).not.toBe(
      buildCacheKey([], [], [], [], []),
    );
  });
});

describe("shouldRestoreSnapshot — #2989 regression", () => {
  it("returns false when there is no cached snapshot", () => {
    expect(shouldRestoreSnapshot(null, buildCacheKey([], [], [], [], []))).toBe(
      false,
    );
  });

  it("returns true when the cached snapshot's cache key matches the URL filters", () => {
    const cached = makeSnapshot({
      keywords: ["python"],
      cacheKey: buildCacheKey(["python"], [], [], [], []),
    });
    const currentKey = buildCacheKey(["python"], [], [], [], []);
    expect(shouldRestoreSnapshot(cached, currentKey)).toBe(true);
  });

  it("returns true when both snapshot and URL have no filters", () => {
    const cached = makeSnapshot({
      cacheKey: buildCacheKey([], [], [], [], []),
    });
    const currentKey = buildCacheKey([], [], [], [], []);
    expect(shouldRestoreSnapshot(cached, currentKey)).toBe(true);
  });

  /**
   * Core #2989 case: snapshot was saved from a previous filtered search
   * that returned 0 results. User navigates to /explore (no URL
   * filters). Without the strict cache-key match, the empty
   * ``companies`` and stale ``keywords`` would leak into the fresh
   * mount and trigger ``ZeroResults``.
   */
  it("does NOT restore a filtered snapshot onto an unfiltered URL (the bug)", () => {
    const cached = makeSnapshot({
      keywords: ["zzzzzznorealkeyword"],
      companies: [],
      totalCompanies: 0,
      cacheKey: buildCacheKey(["zzzzzznorealkeyword"], [], [], [], []),
    });
    const currentKey = buildCacheKey([], [], [], [], []);
    expect(shouldRestoreSnapshot(cached, currentKey)).toBe(false);
  });

  it("does NOT restore an unfiltered snapshot onto a filtered URL", () => {
    const cached = makeSnapshot({
      keywords: [],
      cacheKey: buildCacheKey([], [], [], [], []),
    });
    const currentKey = buildCacheKey(["python"], [], [], [], []);
    expect(shouldRestoreSnapshot(cached, currentKey)).toBe(false);
  });

  it("does NOT restore when the snapshot's location filter differs from the URL", () => {
    const cached = makeSnapshot({
      cacheKey: buildCacheKey([], [42], [], [], []),
    });
    const currentKey = buildCacheKey([], [99], [], [], []);
    expect(shouldRestoreSnapshot(cached, currentKey)).toBe(false);
  });
});
