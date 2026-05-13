import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { isTrivialWatchlist, isQualifyingWatchlist } from "../watchlist-utils";
import { buildWatchlistItemListJsonLd } from "../seo";
import type { WatchlistFilters } from "../actions/watchlists";

describe("isTrivialWatchlist", () => {
  it("returns true for empty filters and zero companies", () => {
    expect(isTrivialWatchlist({}, 0)).toBe(true);
    expect(isTrivialWatchlist(null, 0)).toBe(true);
  });

  it("returns false when any company is tracked", () => {
    expect(isTrivialWatchlist({}, 1)).toBe(false);
  });

  it("returns false when any meaningful filter is set", () => {
    expect(isTrivialWatchlist({ keywords: ["x"] }, 0)).toBe(false);
    expect(isTrivialWatchlist({ locationSlugs: ["zurich"] }, 0)).toBe(false);
    expect(isTrivialWatchlist({ salaryMin: 100000 }, 0)).toBe(false);
  });

  // #3059 — workMode + employmentType were added to WatchlistFilters by
  // #3053 but the trivial-watchlist predicate forgot about them. A
  // public watchlist that filters only on workMode (e.g. remote-only)
  // was being misclassified as trivial and excluded from popular/public
  // listings + Typesense indexing.
  it("returns false when only workMode is set (#3059)", () => {
    expect(isTrivialWatchlist({ workMode: ["remote"] }, 0)).toBe(false);
  });
  it("returns false when only employmentType is set (#3059)", () => {
    expect(
      isTrivialWatchlist({ employmentType: ["full_time"] }, 0),
    ).toBe(false);
  });

  // Drift-guard: every meaningful key in WatchlistFilters must be
  // checked by `isTrivialWatchlist` AND by the SQL predicate
  // `nonTrivialWatchlistPredicate` in `src/lib/actions/watchlists.ts`.
  // If you add a new filter to WatchlistFilters, update both predicates
  // or extend the IGNORED_KEYS allowlist with a justification comment.
  describe("drift-guard vs WatchlistFilters keys", () => {
    // Keys that legitimately do NOT count as "meaningful" filters.
    // `anyCompany` is a UI toggle, `salaryCurrency` is a unit/pref that
    // is paired with salaryMin/salaryMax (which ARE checked). The
    // trivial-predicate comment in watchlist-utils.ts spells this out.
    const IGNORED_KEYS: ReadonlySet<keyof WatchlistFilters> = new Set([
      "anyCompany",
      "salaryCurrency",
    ]);

    // Generate one sample value per key. The runtime `keyof T` trick
    // doesn't survive type erasure, so we list keys explicitly and use
    // the type-system to check we covered them. Adding a key to
    // WatchlistFilters but not to this map triggers a TS compile error
    // via the `Required<WatchlistFilters>` mapping below.
    const SAMPLE_VALUES: { [K in keyof Required<WatchlistFilters>]: NonNullable<WatchlistFilters[K]> } = {
      keywords: ["x"],
      locationSlugs: ["zurich"],
      occupationSlugs: ["software-engineer"],
      senioritySlugs: ["senior"],
      technologySlugs: ["go"],
      workMode: ["remote"],
      employmentType: ["full_time"],
      salaryMin: 100000,
      salaryMax: 200000,
      salaryCurrency: "CHF",
      experienceMin: 3,
      experienceMax: 10,
      anyCompany: true,
    };
    const ALL_KEYS = Object.keys(SAMPLE_VALUES) as (keyof WatchlistFilters)[];

    it.each(
      ALL_KEYS.filter((k) => !IGNORED_KEYS.has(k)).map((k) => [k] as const),
    )("TS predicate flags only-%s as non-trivial", (key) => {
      const filters = { [key]: SAMPLE_VALUES[key] } as WatchlistFilters;
      expect(isTrivialWatchlist(filters, 0)).toBe(false);
    });

    it("SQL predicate mentions every non-ignored WatchlistFilters key", () => {
      // Read the SQL fragment as source text so a future contributor
      // adding a filter sees this test fail until they update the SQL.
      const src = readFileSync(
        join(__dirname, "..", "actions", "watchlists.ts"),
        "utf-8",
      );
      const sqlMatch = src.match(
        /nonTrivialWatchlistPredicate\s*=\s*sql`([\s\S]*?)`/,
      );
      expect(sqlMatch, "nonTrivialWatchlistPredicate sql template literal not found").toBeTruthy();
      const sqlBody = sqlMatch![1];
      for (const key of ALL_KEYS) {
        if (IGNORED_KEYS.has(key)) continue;
        expect(
          sqlBody,
          `SQL predicate missing reference to filter key "${key}"`,
        ).toMatch(new RegExp(`'${key}'`));
      }
    });
  });
});

describe("isQualifyingWatchlist (#2823)", () => {
  // Pin Date.now() so the 7-day age check is deterministic.
  const NOW_MS = new Date("2026-05-15T00:00:00Z").getTime();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_MS));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function within7Days(): string {
    return new Date(NOW_MS - 3 * 24 * 60 * 60 * 1000).toISOString();
  }
  function olderThan7Days(): string {
    return new Date(NOW_MS - 30 * 24 * 60 * 60 * 1000).toISOString();
  }

  it("rejects watchlists newer than 7 days", () => {
    expect(
      isQualifyingWatchlist({
        title: "Big Tech Jobs in Switzerland",
        filters: { occupationSlugs: ["software-engineer"], locationSlugs: ["zurich"] },
        companyCount: 5,
        createdAt: within7Days(),
      }),
    ).toBe(false);
  });

  it("rejects the default 'New watchlist' title regardless of substance", () => {
    expect(
      isQualifyingWatchlist({
        title: "New watchlist",
        filters: { occupationSlugs: ["se"], locationSlugs: ["zurich"] },
        companyCount: 10,
        createdAt: olderThan7Days(),
      }),
    ).toBe(false);
    expect(
      isQualifyingWatchlist({
        title: "  NEW WATCHLIST  ",
        filters: {},
        companyCount: 5,
        createdAt: olderThan7Days(),
      }),
    ).toBe(false);
  });

  it("rejects sub-4-character titles", () => {
    expect(
      isQualifyingWatchlist({
        title: "kdb",
        filters: {},
        companyCount: 5,
        createdAt: olderThan7Days(),
      }),
    ).toBe(false);
    expect(
      isQualifyingWatchlist({
        title: "  ab  ",
        filters: { keywords: ["go"] },
        companyCount: 0,
        createdAt: olderThan7Days(),
      }),
    ).toBe(false);
  });

  it("accepts watchlists with ≥3 companies and a substantive title", () => {
    expect(
      isQualifyingWatchlist({
        title: "Top fintech",
        filters: {},
        companyCount: 3,
        createdAt: olderThan7Days(),
      }),
    ).toBe(true);
  });

  it("accepts watchlists with any keyword filter", () => {
    expect(
      isQualifyingWatchlist({
        title: "Backend hiring",
        filters: { keywords: ["rust"] },
        companyCount: 0,
        createdAt: olderThan7Days(),
      }),
    ).toBe(true);
  });

  it("accepts watchlists with ≥2 taxonomy filters", () => {
    expect(
      isQualifyingWatchlist({
        title: "SWE Zurich",
        filters: { occupationSlugs: ["software-engineer"], locationSlugs: ["zurich"] },
        companyCount: 0,
        createdAt: olderThan7Days(),
      }),
    ).toBe(true);
  });

  it("rejects watchlists with only 1 taxonomy filter and no companies/keywords", () => {
    expect(
      isQualifyingWatchlist({
        title: "Munich",
        filters: { locationSlugs: ["munich"] },
        companyCount: 0,
        createdAt: olderThan7Days(),
      }),
    ).toBe(false);
  });

  it("rejects watchlists with only salary/experience filters", () => {
    // salary/experience-only can apply to anything — too thin to be a
    // useful landing page. Mirrors the SQL `HAVING` predicate.
    expect(
      isQualifyingWatchlist({
        title: "High pay roles",
        filters: { salaryMin: 200000, experienceMin: 5 },
        companyCount: 0,
        createdAt: olderThan7Days(),
      }),
    ).toBe(false);
  });

  it("rejects when createdAt is invalid", () => {
    expect(
      isQualifyingWatchlist({
        title: "Top fintech",
        filters: { keywords: ["x"] },
        companyCount: 5,
        createdAt: "not-a-date",
      }),
    ).toBe(false);
  });
});

describe("buildWatchlistItemListJsonLd (#2823)", () => {
  it("returns null for an empty company list", () => {
    expect(
      buildWatchlistItemListJsonLd({ title: "Empty list", companies: [] }, "en"),
    ).toBeNull();
  });

  it("emits a valid ItemList with Organization items", () => {
    const result = buildWatchlistItemListJsonLd(
      {
        title: "Big Tech Jobs in Switzerland",
        companies: [
          { name: "Google", slug: "google" },
          { name: "Microsoft", slug: "microsoft" },
        ],
      },
      "de",
    );
    expect(result).toEqual({
      "@context": "https://schema.org",
      "@type": "ItemList",
      name: "Big Tech Jobs in Switzerland",
      numberOfItems: 2,
      itemListElement: [
        {
          "@type": "ListItem",
          position: 1,
          item: {
            "@type": "Organization",
            name: "Google",
            url: "https://jseek.co/de/company/google",
          },
        },
        {
          "@type": "ListItem",
          position: 2,
          item: {
            "@type": "Organization",
            name: "Microsoft",
            url: "https://jseek.co/de/company/microsoft",
          },
        },
      ],
    });
  });
});
