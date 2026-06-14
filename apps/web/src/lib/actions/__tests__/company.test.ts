import { beforeEach, describe, expect, it, vi } from "vitest";

// `vi.mock` hoists to the top of the file so its factories cannot close
// over module-scope variables. Use `vi.hoisted` to share mocks between
// the factory and the test bodies.
const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  search: vi.fn(),
  dbExecute: vi.fn(),
  buildFilterString: vi.fn(() => ""),
}));

vi.mock("server-only", () => ({}));

// `cacheLife` / `cacheTag` are no-ops outside a Cache Components-enabled
// runtime — vitest doesn't load `next.config.ts`, so calling the real
// implementations throws "cacheLife() is only available with the
// cacheComponents config". Mock them to silent no-ops; the `'use cache'`
// directive itself is removed by the test transform pipeline (Babel +
// esbuild treat it as a no-op string-statement). Tests exercise the
// underlying Typesense + Postgres branching directly. See #2884 bucket
// 4 — `getCompanyBySlug` migrated from Redis-backed `cached()` to
// `'use cache'` here.
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));

vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));

// Drag in the side-effect-bearing transitive imports as no-ops so the
// "use server" module loads cleanly in vitest.
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
}));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: vi.fn() }));
vi.mock("@/lib/actions/locations", () => ({
  expandLocationIds: vi.fn(),
  expandLocationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn(),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/search", () => ({ getSearchProvider: vi.fn() }));
vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_COMPANIES: 5,
  ANON_MAX_POSTINGS: 10,
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  POSTING_BASE_FILTER: "is_active:true && has_content:!=false",
  buildFilterString: mocks.buildFilterString,
}));
vi.mock("@/lib/search/pg-filters", () => ({ localesOrNoneClause: vi.fn() }));
vi.mock("@/lib/actions/search-input", () => ({ parseSearchFilters: vi.fn() }));
vi.mock("@/lib/search/params", () => ({
  firstOf: vi.fn(),
  idsOrUndefined: vi.fn(),
  parseRangeParam: vi.fn(),
}));

import { getCompanyBySlug, searchCompaniesForWatchlist } from "../company";

const searchMock = mocks.search;
const dbExecuteMock = mocks.dbExecute;
const cacheLifeMock = mocks.cacheLife;
const cacheTagMock = mocks.cacheTag;
const buildFilterStringMock = mocks.buildFilterString;

const _hit = (overrides: Record<string, unknown> = {}) => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: "https://cdn.x/icon.png",
  logo: null,
  website: "https://acme.example",
  description: "We build things in English.",
  description_de: "Wir bauen Dinge.",
  industry_id: 7,
  industry_name: "Software",
  industry_name_de: "Software",
  employee_count_range: 3,
  founded_year: 2015,
  active_posting_count: 42,
  ...overrides,
});

const _typesenseResponse = (hit: Record<string, unknown> | null) =>
  hit ? { hits: [{ document: hit }] } : { hits: [] };

beforeEach(() => {
  vi.clearAllMocks();
  searchMock.mockReset();
  dbExecuteMock.mockReset();
  buildFilterStringMock.mockReset();
  buildFilterStringMock.mockReturnValue("");
});

describe("searchCompaniesForWatchlist", () => {
  it("includes companies with zero active postings in unfiltered search", async () => {
    searchMock.mockResolvedValue({
      found: 1,
      hits: [{ document: _hit({ active_posting_count: 0 }) }],
    });

    const out = await searchCompaniesForWatchlist({
      query: "Acme",
      locale: "en",
      offset: 0,
      limit: 20,
    });

    expect(out.companies).toHaveLength(1);
    expect(out.companies[0].activeMatches).toBe(0);
    expect(searchMock).toHaveBeenCalledWith(
      expect.not.objectContaining({ filter_by: expect.stringContaining("active_posting_count") }),
    );
  });

  it("does not exclude zero-posting companies when filtering by industry", async () => {
    searchMock.mockResolvedValue({
      found: 1,
      hits: [{ document: _hit({ active_posting_count: 0 }) }],
    });

    await searchCompaniesForWatchlist({
      industryId: 7,
      locale: "en",
      offset: 0,
      limit: 20,
    });

    expect(searchMock).toHaveBeenCalledWith(
      expect.objectContaining({ filter_by: "industry_id:=7" }),
    );
  });

  it("keeps starred ordering without requiring active postings", async () => {
    searchMock
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: _hit({ active_posting_count: 0 }) }],
      })
      .mockResolvedValueOnce({ found: 0, hits: [] });

    const out = await searchCompaniesForWatchlist({
      locale: "en",
      offset: 0,
      limit: 20,
      starredCompanyIds: ["co-1"],
    });

    expect(out.companies).toHaveLength(1);
    expect(searchMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ filter_by: "id:[co-1]" }),
    );
    expect(searchMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ filter_by: "id:!=[co-1]" }),
    );
  });

  it("includes a searched zero-posting company when the watchlist has filters", async () => {
    buildFilterStringMock.mockReturnValue("location_ids:=[42]");
    searchMock
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: _hit({ active_posting_count: 0 }) }],
      })
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        hits: [{ document: _hit({ active_posting_count: 0 }) }],
      });

    const out = await searchCompaniesForWatchlist({
      query: "Acme",
      locale: "en",
      offset: 0,
      limit: 20,
      locationIds: [42],
    });

    expect(out).toMatchObject({
      total: 1,
      companies: [{ id: "co-1", activeMatches: 0 }],
    });
  });

  it("includes a searched active company with zero matches for the current watchlist filters", async () => {
    buildFilterStringMock.mockReturnValue("technology_ids:=[99]");
    searchMock
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: _hit({ active_posting_count: 42 }) }],
      })
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        hits: [{ document: _hit({ active_posting_count: 42 }) }],
      });

    const out = await searchCompaniesForWatchlist({
      query: "Acme",
      locale: "en",
      offset: 0,
      limit: 20,
      technologyIds: [99],
    });

    expect(out).toMatchObject({
      total: 1,
      companies: [{ id: "co-1", activeMatches: 0 }],
    });
  });
});

describe("getCompanyBySlug — Typesense path", () => {
  it("returns the Typesense hit mapped to CompanyDetail", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(_hit()));

    const out = await getCompanyBySlug("acme", "en");

    expect(out).toEqual({
      id: "co-1",
      name: "Acme Corp",
      slug: "acme",
      icon: "https://cdn.x/icon.png",
      logo: null,
      website: "https://acme.example",
      description: "We build things in English.",
      industryId: 7,
      industryName: "Software",
      employeeCountRange: 3,
      foundedYear: 2015,
      activeJobCount: 42,
    });
    expect(searchMock).toHaveBeenCalledWith({
      q: "*",
      filter_by: "slug:=acme",
      per_page: 1,
    });
    expect(dbExecuteMock).not.toHaveBeenCalled();
  });

  it("prefers the locale-specific description when present", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(_hit()));
    const out = await getCompanyBySlug("acme", "de");
    expect(out?.description).toBe("Wir bauen Dinge.");
    expect(out?.industryName).toBe("Software");
  });

  it("falls back to base English description when locale variant is missing", async () => {
    searchMock.mockResolvedValue(
      _typesenseResponse(_hit({ description_fr: undefined })),
    );
    const out = await getCompanyBySlug("acme", "fr");
    expect(out?.description).toBe("We build things in English.");
  });

  it("returns null description when both locale and base are missing", async () => {
    searchMock.mockResolvedValue(
      _typesenseResponse(
        _hit({ description: undefined, description_de: undefined }),
      ),
    );
    const out = await getCompanyBySlug("acme", "de");
    expect(out?.description).toBeNull();
  });

  it("returns null description when locale value is empty string", async () => {
    /** Empty string must be treated as missing so the en fallback fires —
     * otherwise a half-translated company shows the empty string verbatim. */
    searchMock.mockResolvedValue(_typesenseResponse(_hit({ description_de: "" })));
    const out = await getCompanyBySlug("acme", "de");
    expect(out?.description).toBe("We build things in English.");
  });
});

describe("getCompanyBySlug — slug shape guard", () => {
  /** SLUG_SHAPE rejection short-circuits the Typesense call so attacker input
   * never reaches the raw-interpolated `filter_by`. Postgres still runs
   * (drizzle parameterizes; injection-safe) and returns null naturally. */
  it.each([
    "acme corp", // space
    "acme&&filter:=evil", // injection attempt
    "ACME", // uppercase
    "acme--double", // double hyphen
    "-acme", // leading hyphen
    "acme-", // trailing hyphen
    "acme/path", // slash
    "", // empty
  ])(
    "rejects malformed slug %j without calling Typesense (Postgres still queried, returns null)",
    async (slug) => {
      dbExecuteMock.mockResolvedValue([]);
      const out = await getCompanyBySlug(slug, "en");
      expect(out).toBeNull();
      expect(searchMock).not.toHaveBeenCalled();
      expect(dbExecuteMock).toHaveBeenCalled();
    },
  );

  it.each(["acme", "1-800-flowers", "deutsche-bank", "abc123-xyz"])(
    "accepts well-formed slug %j",
    async (slug) => {
      searchMock.mockResolvedValue(_typesenseResponse(_hit({ slug })));
      const out = await getCompanyBySlug(slug, "en");
      expect(out?.slug).toBe(slug);
    },
  );
});

describe("getCompanyBySlug — Postgres fallback", () => {
  const _pgRow = {
    id: "co-1",
    name: "Acme Corp",
    slug: "acme",
    icon: null,
    logo: null,
    website: null,
    description: "From Postgres",
    industry_id: 7,
    industry_name: "Software",
    employee_count_range: 3,
    founded_year: 2015,
  };

  it("falls through to Postgres when Typesense throws", async () => {
    searchMock.mockRejectedValue(new Error("typesense down"));
    dbExecuteMock.mockResolvedValue([_pgRow]);

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    expect(out?.activeJobCount).toBe(0); // PG path doesn't compute this
    expect(dbExecuteMock).toHaveBeenCalled();
  });

  it("logs `[company] Typesense failed, falling back to Postgres` when Typesense throws (so fallback rate is queryable per #3175)", async () => {
    /** Issue colophon-group/jobseek#3175 — the silent `} catch {}` made a
     * Cloudflare-tunnel outage indistinguishable from healthy traffic in
     * the logs. Matches the precedent set by `searchCompaniesForWatchlist`
     * and `_fetchSimilarUnfiltered` in the same file. */
    searchMock.mockRejectedValue(new Error("typesense unreachable"));
    dbExecuteMock.mockResolvedValue([_pgRow]);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const out = await getCompanyBySlug("acme", "en");

    // Fallback behaviour preserved — Postgres still serves the request.
    expect(out?.description).toBe("From Postgres");
    // Stable event prefix so the fallback rate is queryable in Loki.
    const fallbackCalls = errorSpy.mock.calls.filter(
      (call) => call[0] === "[company] Typesense failed, falling back to Postgres",
    );
    expect(fallbackCalls).toHaveLength(1);
    expect(fallbackCalls[0][1]).toBeInstanceOf(Error);
    expect((fallbackCalls[0][1] as Error).message).toBe("typesense unreachable");
    errorSpy.mockRestore();
  });

  it("does NOT log the fallback event when Typesense returns 0 hits (only thrown errors signal an outage)", async () => {
    /** Zero hits is the brand-new-company path, not a Typesense outage —
     * silencing it here keeps the fallback-rate metric an accurate
     * signal of Typesense health, not company-freshness noise. */
    searchMock.mockResolvedValue(_typesenseResponse(null));
    dbExecuteMock.mockResolvedValue([_pgRow]);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    const fallbackCalls = errorSpy.mock.calls.filter(
      (call) => call[0] === "[company] Typesense failed, falling back to Postgres",
    );
    expect(fallbackCalls).toHaveLength(0);
    errorSpy.mockRestore();
  });

  it("falls through to Postgres when Typesense returns 0 hits", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(null));
    dbExecuteMock.mockResolvedValue([_pgRow]);

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    expect(dbExecuteMock).toHaveBeenCalled();
  });

  it("returns null when both Typesense and Postgres miss", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(null));
    dbExecuteMock.mockResolvedValue([]);

    const out = await getCompanyBySlug("acme", "en");
    expect(out).toBeNull();
  });

  it("retries the Postgres query on transient ECONNRESET (#2918 follow-up)", async () => {
    /** The 2026-05-09 Vercel build died on a single `read ECONNRESET`
     * during this exact code path (OG-image prerender →
     * `_fetchCompanyBySlugFromPostgres`). The next build 2 min later
     * succeeded — a flake, not a structural break. The retry helper
     * must turn this single transient error into a successful query
     * so the prerender finishes. */
    searchMock.mockRejectedValue(new Error("typesense down"));
    const econn = new Error("read ECONNRESET") as Error & { code: string };
    econn.code = "ECONNRESET";
    dbExecuteMock
      .mockRejectedValueOnce(econn)
      .mockResolvedValueOnce([_pgRow]);
    const warnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => {});

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});

describe("getCompanyBySlug — caching contract", () => {
  it("calls cacheLife('hours') + cacheTag(company:slug) + cacheTag(company-csv-data) on a hit", async () => {
    /** #2884 bucket 4 footgun migration: under `'use cache'` a
     * `null` return would pin a brand-new slug for the cacheLife
     * window. The wrapper throws `CompanyNotFoundError` past the
     * cache boundary on null and the outer catches → returns null,
     * keeping the slot empty. The contract this test asserts is the
     * tagging surface (so the page-level `revalidateTag` and the
     * `/api/internal/invalidate-typeahead` CSV-driven sweep both
     * still drop the slot when needed). The null-handling itself is
     * covered by the Postgres-fallback `returns null` test above. */
    searchMock.mockResolvedValue(_typesenseResponse(_hit()));

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.id).toBe("co-1");
    expect(cacheLifeMock).toHaveBeenCalledWith("hours");
    const tags = cacheTagMock.mock.calls.map((c) => c[0]);
    expect(tags).toContain("company:acme");
    expect(tags).toContain("company-csv-data");
  });

  it("returns null without re-throwing CompanyNotFoundError on miss", async () => {
    /** Regression for the throw-and-catch wrapper: the inner
     * `_fetchCompanyBySlugCached` throws `CompanyNotFoundError` so
     * the `'use cache'` slot stays empty for the next attempt. The
     * outer `getCompanyBySlug` MUST swallow that one error class
     * and return null — leaking the throw would 500 the route. */
    searchMock.mockResolvedValue(_typesenseResponse(null));
    dbExecuteMock.mockResolvedValue([]);

    const out = await getCompanyBySlug("ghost-slug", "en");
    expect(out).toBeNull();
  });

  it("re-throws unexpected errors past the wrapper", async () => {
    /** Empirical guard for #2884 bucket 4: the catch-and-null path is
     * scoped to `CompanyNotFoundError` only. A genuine downstream
     * failure (DB pool exhausted, Drizzle parse error, …) MUST
     * propagate so Suspense / error boundaries trigger; silently
     * nulling here would surface as a 404 instead of a 5xx and hide
     * the outage. */
    searchMock.mockRejectedValue(new Error("typesense down"));
    const boom = new Error("postgres pool exhausted");
    dbExecuteMock.mockRejectedValue(boom);

    await expect(getCompanyBySlug("acme", "en")).rejects.toBe(boom);
  });
});
