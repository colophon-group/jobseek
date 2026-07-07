/**
 * Perf regression test (issue #3176).
 *
 * Asserts that the watchlist listing surfaces — `getUserWatchlists`,
 * `getPopularWatchlists`, and `searchPublicWatchlists` — do NOT fan out
 * per-watchlist Typesense `job_posting` count queries. Filtered rows
 * may request precise counts, but they must do it in a single
 * `multi_search` batch per listing render (#3261).
 *
 * Pre-fix:
 *   `getUserWatchlists(locale)` loaded N watchlist rows from Postgres,
 *   then ran `Promise.all(rows.map(resolveFilteredJobCount))`, each
 *   firing a Typesense filtered count against `job_posting`. Free users
 *   (5 watchlists) paid 5 round-trips; paid users (50 watchlists) paid
 *   50 round-trips on every `/watchlists` load.
 *
 *   `searchPublicWatchlists` / `getPopularWatchlists` queried the
 *   Typesense `watchlist` collection (1 round-trip), then re-ran the
 *   same N-fanout via `_enrichWatchlistsWithRealCounts`.
 *
 * #3176 post-fix:
 *   - `getUserWatchlists`  one SQL query against Postgres with the
 *     active count denormalized via a `watchlist_company JOIN
 *     job_posting WHERE is_active` subquery. Zero Typesense round-trips
 *     for unfiltered company-scoped rows.
 *   - `searchPublicWatchlists` / `getPopularWatchlists`  one Typesense
 *     `watchlist` collection search whose returned docs already carry
 *     `active_job_count` for company-scoped watchlists. `anyCompany`
 *     rows use a self-contained `filters_json` payload from the same
 *     hit and run a bounded live `job_posting` count without Postgres
 *     hydration.
 *
 * #3261 post-fix:
 *   filtered rows patch the denormalized count through one batched
 *   `multi_search`, using viewer languages and the same filter shape as
 *   the watchlist-detail active count.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => {
  type SqlChunk = { text: string; values: unknown[] };

  function isSqlChunk(value: unknown): value is SqlChunk {
    return (
      typeof value === "object" &&
      value !== null &&
      "text" in value &&
      "values" in value
    );
  }

  const sqlTag = Object.assign(
    (strings: TemplateStringsArray, ...values: unknown[]): SqlChunk => {
      const chunk: SqlChunk = { text: "", values: [] };
      strings.forEach((part, index) => {
        chunk.text += part;
        if (index >= values.length) return;
        const value = values[index];
        if (isSqlChunk(value)) {
          chunk.text += value.text;
          chunk.values.push(...value.values);
        } else {
          chunk.text += "?";
          chunk.values.push(value);
        }
      });
      return chunk;
    },
    {
      join: (chunks: SqlChunk[], separator: SqlChunk): SqlChunk => ({
        text: chunks.map((chunk) => chunk.text).join(separator.text),
        values: chunks.flatMap((chunk, index) =>
          index === 0 ? chunk.values : [...separator.values, ...chunk.values],
        ),
      }),
    },
  );

  return {
    getSessionUserId: vi.fn(),
    dbExecute: vi.fn(),
    withDbRetry: vi.fn(),
    cached: vi.fn(),
    sqlTag,

    // Typesense search call counter — every collection().documents().search()
    // routes through this one mock so we can assert call count + which
    // collection was hit.
    tsSearch: vi.fn(),
    tsMultiSearch: vi.fn(),
    tsCollectionsCalls: [] as string[],

    getViewerLanguages: vi.fn().mockResolvedValue(["en"]),
    resolveLocationSlugs: vi.fn(),
    resolveOccupationSlugs: vi.fn(),
    resolveSenioritySlugs: vi.fn(),
    resolveTechnologySlugs: vi.fn(),
    canCreateWatchlist: vi.fn().mockResolvedValue({ allowed: true }),
    notifyIndexNow: vi.fn(),
    tsUpsertWatchlist: vi.fn(),
    tsDeleteWatchlist: vi.fn(),
    tsUpdateWatchlistField: vi.fn(),
    generateUniqueSlug: vi.fn(),
    // #3201: pass-through that runs the inserter once with the picker's
    // slug. Listing-perf tests don't exercise the createWatchlist path
    // but the module-level import needs the export.
    insertWatchlistWithUniqueSlug: vi.fn(
      async (
        userId: string,
        title: string,
        insert: (slug: string) => Promise<unknown>,
      ) => {
        const slug = await mocks.generateUniqueSlug(userId, title);
        const row = await insert(slug);
        return { row, slug };
      },
    ),
  };
});

vi.mock("next/server", () => ({ after: (cb: () => unknown) => cb() }));
vi.mock("next/cache", () => ({ updateTag: vi.fn() }));

vi.mock("@/lib/cache", () => ({
  // `cached(key, factory, opts)` — execute the factory immediately so
  // listing surfaces under test produce a real result, but record the
  // call for assertions where useful.
  cached: vi.fn((_key: string, factory: () => Promise<unknown>) => {
    mocks.cached(_key);
    return factory();
  }),
  invalidate: vi.fn(),
  invalidatePattern: vi.fn(),
}));

vi.mock("@/lib/cache-ttl", () => ({
  CACHE_TTL_SHORT: 60,
  CACHE_TTL_POPULAR: 120,
  CACHE_TTL_MEDIUM: 300,
  CACHE_TTL_LONG: 3600,
}));

vi.mock("@/lib/db-retry", () => ({
  withDbRetry: vi.fn((fn: () => Promise<unknown>) => {
    mocks.withDbRetry();
    return fn();
  }),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/lib/viewer", () => ({
  getViewerLanguages: mocks.getViewerLanguages,
}));

vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: mocks.canCreateWatchlist,
  getUserPlan: vi.fn().mockResolvedValue("free"),
  PLAN_LIMITS: { free: { canReceiveAlerts: false }, paid: { canReceiveAlerts: true } },
}));

vi.mock("@/lib/indexnow", () => ({ notifyIndexNow: mocks.notifyIndexNow }));

vi.mock("@/lib/search/typesense-watchlist", () => ({
  upsertWatchlist: mocks.tsUpsertWatchlist,
  deleteWatchlist: mocks.tsDeleteWatchlist,
  updateWatchlistField: mocks.tsUpdateWatchlistField,
}));

vi.mock("@/lib/watchlist-slug", () => ({
  generateUniqueSlug: mocks.generateUniqueSlug,
  insertWatchlistWithUniqueSlug: mocks.insertWatchlistWithUniqueSlug,
}));

// One mock entry point for ALL Typesense reads. Every
// `client.collections(name).documents().search(...)` routes through
// `mocks.tsSearch`; the collection name is captured so tests can
// assert which collections were hit and how often.
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    multiSearch: {
      perform: mocks.tsMultiSearch,
    },
    collections: (name: string) => {
      mocks.tsCollectionsCalls.push(name);
      return {
        documents: () => ({
          search: mocks.tsSearch,
        }),
      };
    },
  }),
}));

vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: vi.fn((filters?: Record<string, unknown> | null) => {
    if (!filters) return "";
    const parts: string[] = [];
    if (Array.isArray(filters.locationIds) && filters.locationIds.length) {
      parts.push(`location_ids:[${filters.locationIds.join(",")}]`);
    }
    if (Array.isArray(filters.occupationIds) && filters.occupationIds.length) {
      parts.push(`occupation_ids:[${filters.occupationIds.join(",")}]`);
    }
    if (Array.isArray(filters.seniorityIds) && filters.seniorityIds.length) {
      parts.push(`seniority_id:[${filters.seniorityIds.join(",")}]`);
    }
    if (Array.isArray(filters.technologyIds) && filters.technologyIds.length) {
      parts.push(`technology_ids:[${filters.technologyIds.join(",")}]`);
    }
    if (Array.isArray(filters.languages) && filters.languages.length) {
      parts.push(`locales:[${[...filters.languages, "_none"].join(",")}]`);
    }
    return parts.join(" && ");
  }),
  POSTING_BASE_FILTER: "is_active:true",
  POSTING_FLOW_FILTER: "has_content:!=false",
}));

vi.mock("@/lib/search/pg-filters", () => ({
  localesOrNoneClause: vi.fn(),
}));

vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_WATCHLIST_POSTINGS: 50,
  COMPANY_BATCH_SIZE: 100,
}));

vi.mock("@/lib/actions/locations", () => ({
  expandLocationIds: vi.fn().mockResolvedValue([]),
  expandLocationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveLocationSlugs: mocks.resolveLocationSlugs,
}));

vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn().mockResolvedValue([]),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));

vi.mock("@/lib/services/taxonomy", () => ({
  expandOccupationIds: vi.fn().mockResolvedValue([]),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));

vi.mock("drizzle-orm", () => ({
  sql: mocks.sqlTag,
  eq: (..._args: unknown[]) => ({ _isEq: true }),
  and: (..._args: unknown[]) => ({ _isAnd: true }),
}));

vi.mock("@/db/schema", () => ({
  watchlist: {},
  watchlistCompany: {},
  company: {},
}));

vi.mock("@/db", () => ({
  db: {
    execute: (...args: unknown[]) => mocks.dbExecute(...args),
  },
}));

// Module under test must be imported AFTER all vi.mock factories.
import {
  getUserWatchlists,
  getPopularWatchlists,
  searchPublicWatchlists,
} from "../watchlists";

const USER_ID = "user-1";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.tsCollectionsCalls.length = 0;
  mocks.getSessionUserId.mockResolvedValue(USER_ID);
  mocks.canCreateWatchlist.mockResolvedValue({ allowed: true });
  mocks.getViewerLanguages.mockResolvedValue(["en"]);
  mocks.resolveLocationSlugs.mockResolvedValue(new Map([
    ["zurich", { id: 2657896, slug: "zurich", name: "Zurich" }],
    ["switzerland", { id: 2658434, slug: "switzerland", name: "Switzerland" }],
  ]));
  mocks.resolveOccupationSlugs.mockResolvedValue(new Map([
    ["software-engineer", { id: 1, slug: "software-engineer", name: "Software Engineer" }],
  ]));
  mocks.resolveSenioritySlugs.mockResolvedValue(new Map());
  mocks.resolveTechnologySlugs.mockResolvedValue(new Map());
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- helpers ----------------------------------------------------------

function fakeUserWatchlistRow(
  i: number,
  activeJobCount: number,
  overrides: Partial<{
    filters: Record<string, unknown>;
    company_ids: string[];
    company_count: number;
  }> = {},
) {
  return {
    id: `wl-${i}`,
    slug: `slug-${i}`,
    title: `Watchlist ${i}`,
    description: null,
    is_public: i % 2 === 0,
    alerts_enabled: false,
    filters: overrides.filters ?? {},
    last_accessed_at: new Date("2026-05-14T00:00:00Z"),
    created_at: new Date("2026-05-01T00:00:00Z"),
    company_count: overrides.company_count ?? 3,
    active_job_count: activeJobCount,
    company_ids: overrides.company_ids ?? [`company-${i}-a`, `company-${i}-b`],
  };
}

function fakePublicWatchlistHit(
  i: number,
  activeJobCount: number,
  filters?: Record<string, unknown>,
) {
  return {
    document: {
      id: `wl-${i}`,
      slug: `slug-${i}`,
      title: `Public Watchlist ${i}`,
      description: `Description ${i}`,
      owner_name: "Alice",
      owner_username: "alice",
      company_count: 5,
      active_job_count: activeJobCount,
      mirror_count: 2,
      is_featured: false,
      has_description: true,
      created_at: 1715644800,
      is_public: true,
      ...(filters ? { filters_json: JSON.stringify(filters) } : {}),
    },
  };
}

// ---- getUserWatchlists (user's own listing page) -----------------------

describe("getUserWatchlists — listing fan-out fix (#3176)", () => {
  it("fires ZERO Typesense queries when loading N watchlists", async () => {
    const N = 50; // paid-tier user, worst-case
    const rows = Array.from({ length: N }, (_, i) => fakeUserWatchlistRow(i, i * 3));
    mocks.dbExecute.mockResolvedValueOnce(rows);

    await getUserWatchlists("en");

    expect(mocks.tsSearch).not.toHaveBeenCalled();
    expect(mocks.tsCollectionsCalls).toEqual([]);
  });

  it("issues exactly ONE Postgres query for N watchlists (not 1 + 5N)", async () => {
    const N = 20;
    const rows = Array.from({ length: N }, (_, i) => fakeUserWatchlistRow(i, i));
    mocks.dbExecute.mockResolvedValueOnce(rows);

    await getUserWatchlists("en");

    // Only the single SELECT that loads watchlists + their denormalized
    // active_job_count. Pre-fix this would have been 1 (rows) plus up
    // to 4N (taxonomy lookups for resolveFilteredJobCount) plus N
    // (Typesense count).
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
  });

  it("returns the denormalized active_job_count straight from SQL", async () => {
    const rows = [
      fakeUserWatchlistRow(0, 42),
      fakeUserWatchlistRow(1, 7),
      fakeUserWatchlistRow(2, 0),
    ];
    mocks.dbExecute.mockResolvedValueOnce(rows);

    const result = await getUserWatchlists("en");

    expect(result).toHaveLength(3);
    expect(result.map((w) => w.activeJobCount)).toEqual([42, 7, 0]);
  });

  it("patches filtered company-scoped rows with one batched Typesense multi_search (#3261)", async () => {
    const rows = [
      fakeUserWatchlistRow(0, 108, {
        filters: {
          locationSlugs: ["switzerland"],
          occupationSlugs: ["software-engineer"],
        },
        company_ids: ["company-a", "company-b"],
      }),
      fakeUserWatchlistRow(1, 44, {
        filters: {
          keywords: ["python"],
        },
        company_ids: ["company-c"],
      }),
      fakeUserWatchlistRow(2, 12),
    ];
    mocks.dbExecute.mockResolvedValueOnce(rows);
    mocks.tsMultiSearch.mockResolvedValueOnce({
      results: [
        { found: 6, hits: [] },
        { found: 3, hits: [] },
      ],
    });

    const result = await getUserWatchlists("en");

    expect(result.map((w) => w.activeJobCount)).toEqual([6, 3, 12]);
    expect(mocks.tsSearch).not.toHaveBeenCalled();
    expect(mocks.tsMultiSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch.mock.calls[0]?.[0]).toMatchObject({
      searches: [
        {
          collection: "job_posting",
          q: "*",
          query_by: "title",
          per_page: 0,
        },
        {
          collection: "job_posting",
          q: "python",
          query_by: "title",
          per_page: 0,
        },
      ],
    });
    const searches = mocks.tsMultiSearch.mock.calls[0]?.[0].searches;
    expect(searches[0].filter_by).toContain("company_id:[company-a,company-b]");
    expect(searches[0].filter_by).toContain("location_ids:[2658434]");
    expect(searches[0].filter_by).toContain("occupation_ids:[1]");
    expect(searches[0].filter_by).toContain("locales:[en,_none]");
    expect(searches[1].filter_by).toContain("company_id:[company-c]");
  });

  it("returns [] without touching the database when unauthenticated", async () => {
    mocks.getSessionUserId.mockResolvedValueOnce(null);

    const result = await getUserWatchlists("en");

    expect(result).toEqual([]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
    expect(mocks.tsSearch).not.toHaveBeenCalled();
  });

  // Regression for #3333. `anyCompany` watchlists have no `watchlist_company`
  // rows, so the SQL JOIN-subquery `active_job_count` is always 0 for them.
  // The fix patches the count in JS by running one Typesense `per_page: 0`
  // count per `anyCompany` row.
  it("patches anyCompany watchlists with a live Typesense count", async () => {
    const anyRow = {
      ...fakeUserWatchlistRow(0, 0),
      filters: { anyCompany: true, locationSlugs: ["eu"] },
    };
    const normalRow = fakeUserWatchlistRow(1, 12);
    mocks.dbExecute.mockResolvedValueOnce([anyRow, normalRow]);
    mocks.resolveLocationSlugs.mockResolvedValueOnce(new Map([
      ["eu", { id: 6255148, slug: "eu", name: "European Union" }],
    ]));
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 38717, hits: [] }] });

    const result = await getUserWatchlists("en");

    expect(result).toHaveLength(2);
    // Patched from SQL's 0 to the Typesense count.
    expect(result[0].activeJobCount).toBe(38717);
    // Non-anyCompany row keeps the SQL count untouched — no extra
    // round-trip fires for it.
    expect(result[1].activeJobCount).toBe(12);

    // Exactly one Typesense call (the anyCompany count). The normal
    // row goes straight from SQL.
    expect(mocks.tsSearch).not.toHaveBeenCalled();
    expect(mocks.tsMultiSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual([]);
  });

  it("returns 0 (not the SQL 0 either) when the anyCompany Typesense count fails", async () => {
    const anyRow = {
      ...fakeUserWatchlistRow(0, 0),
      filters: { anyCompany: true, locationSlugs: ["eu"] },
    };
    mocks.dbExecute.mockResolvedValueOnce([anyRow]);
    mocks.resolveLocationSlugs.mockResolvedValueOnce(new Map([
      ["eu", { id: 6255148, slug: "eu", name: "European Union" }],
    ]));
    mocks.tsMultiSearch.mockRejectedValueOnce(new Error("typesense unreachable"));

    const result = await getUserWatchlists("en");

    expect(result).toHaveLength(1);
    expect(result[0].activeJobCount).toBe(0); // graceful degradation
  });

  it("fires no Typesense queries when there are zero anyCompany rows", async () => {
    const rows = Array.from({ length: 10 }, (_, i) => fakeUserWatchlistRow(i, i * 3));
    mocks.dbExecute.mockResolvedValueOnce(rows);

    await getUserWatchlists("en");

    expect(mocks.tsSearch).not.toHaveBeenCalled();
  });

  // Regression for #3344. The `anyCompany` Typesense patch previously
  // omitted the viewer-language scope, so the tile count diverged from
  // the watchlist-detail page (which DOES scope by `locales:[lang,
  // _none]` via `_getWatchlistPostingsTypesense`). The batched listing
  // count path must keep forwarding the viewer's resolved languages so
  // both surfaces issue the same Typesense filter shape.
  it("passes viewer languages through to the anyCompany Typesense count (#3344)", async () => {
    const anyRow = {
      ...fakeUserWatchlistRow(0, 0),
      filters: { anyCompany: true, locationSlugs: ["eu"] },
    };
    mocks.dbExecute.mockResolvedValueOnce([anyRow]);
    mocks.getViewerLanguages.mockResolvedValueOnce(["en"]);
    mocks.resolveLocationSlugs.mockResolvedValueOnce(new Map([
      ["eu", { id: 6255148, slug: "eu", name: "European Union" }],
    ]));
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 39126, hits: [] }] });

    const buildFilterStringMock = vi.mocked(
      await import("@/lib/search/typesense-filters"),
    ).buildFilterString;
    buildFilterStringMock.mockClear();

    await getUserWatchlists("en");

    // `getViewerLanguages` was called once with the page locale.
    expect(mocks.getViewerLanguages).toHaveBeenCalledWith("en");
    // The viewer's languages were forwarded into `buildFilterString` so
    // the Typesense filter for the tile matches the detail page's
    // shape (locales:[en,_none] in the rendered filter_by string).
    const lastCall = buildFilterStringMock.mock.calls.at(-1);
    expect(lastCall?.[0]).toMatchObject({ languages: ["en"] });
  });
});

// ---- public Discover surfaces (Typesense path) -------------------------

describe("getPopularWatchlists — Typesense path (#3176)", () => {
  it("issues exactly ONE Typesense query (watchlist collection) — no per-row job_posting fan-out", async () => {
    const N = 20;
    const hits = Array.from({ length: N }, (_, i) => fakePublicWatchlistHit(i, i * 5));
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: N });

    await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    // Only the watchlist collection — no per-row `job_posting` count
    // (the regression this test protects against from #3176).
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("preserves the denormalized active_job_count from the Typesense doc for non-anyCompany rows", async () => {
    const hits = [
      fakePublicWatchlistHit(0, 100),
      fakePublicWatchlistHit(1, 50),
      fakePublicWatchlistHit(2, 25),
    ];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 3 });

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(result.watchlists.map((w) => w.activeJobCount)).toEqual([100, 50, 25]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("does not hydrate filters from Postgres for Discover cards (#3492)", async () => {
    const hits = [
      fakePublicWatchlistHit(0, 10),
      fakePublicWatchlistHit(1, 20),
      fakePublicWatchlistHit(2, 30),
    ];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 3 });

    await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });
});

describe("searchPublicWatchlists — Typesense path (#3176)", () => {
  it("issues exactly ONE Typesense query — no per-row job_posting fan-out", async () => {
    const N = 15;
    const hits = Array.from({ length: N }, (_, i) => fakePublicWatchlistHit(i, i + 1));
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: N });

    await searchPublicWatchlists({ query: "python", offset: 0, limit: 20, locale: "en" });

    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("preserves the denormalized active_job_count from the Typesense doc for non-anyCompany rows", async () => {
    const hits = [
      fakePublicWatchlistHit(0, 99),
      fakePublicWatchlistHit(1, 77),
    ];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 2 });

    const result = await searchPublicWatchlists({
      query: "python",
      offset: 0,
      limit: 20,
      locale: "en",
    });

    expect(result.watchlists.map((w) => w.activeJobCount)).toEqual([99, 77]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("does not issue the old Discover filter lookup (#3492)", async () => {
    const hits = [
      fakePublicWatchlistHit(0, 99),
      fakePublicWatchlistHit(1, 77),
    ];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 2 });

    await searchPublicWatchlists({
      query: "enterprise sales",
      offset: 0,
      limit: 20,
      locale: "en",
    });

    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("short-circuits empty query without any I/O", async () => {
    const result = await searchPublicWatchlists({
      query: "   ",
      offset: 0,
      limit: 20,
      locale: "en",
    });

    expect(result).toEqual({ watchlists: [], total: 0 });
    expect(mocks.tsSearch).not.toHaveBeenCalled();
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });
});

// ---- Discover anyCompany patch (#3352) -------------------------------
//
// `getPopularWatchlists` and `searchPublicWatchlists` read
// `active_job_count` straight from the Typesense `watchlist` doc, which
// is 0 by construction for `anyCompany` watchlists (the crawler's
// `refresh_typesense_counts` joins the empty `watchlist_company` table
// for them — see `apps/crawler/src/sync.py`). The public fix stores a
// sanitized, self-contained `filters_json` payload on the Typesense
// watchlist doc, then runs one `job_posting` count per `anyCompany`
// row in parallel — bounded by page size on the Discover surface and
// without Postgres filter hydration.

describe("getPopularWatchlists — anyCompany count patch (#3352)", () => {
  it("patches anyCompany rows with a live Typesense count", async () => {
    const anyHit = fakePublicWatchlistHit(0, 0, {
      anyCompany: true,
      locationSlugs: ["eu"],
      locationIds: [100],
    }); // Typesense doc says 0
    const normalHit = fakePublicWatchlistHit(1, 12);
    mocks.tsSearch.mockResolvedValueOnce({ hits: [anyHit, normalHit], found: 2 });
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 38717, hits: [] }] });

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(result.watchlists).toHaveLength(2);
    // The anyCompany row: patched from Typesense doc's 0 to the live count.
    expect(result.watchlists[0].activeJobCount).toBe(38717);
    // The non-anyCompany row: denormalized doc count survives unchanged.
    expect(result.watchlists[1].activeJobCount).toBe(12);

    // One watchlist search plus one batched job_posting multi_search.
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("patches filtered company-scoped rows with one company-id query and one multi_search (#3261)", async () => {
    const filteredHit = fakePublicWatchlistHit(0, 1000, {
      locationSlugs: ["switzerland"],
      locationIds: [2658434],
      occupationSlugs: ["software-engineer"],
      occupationIds: [1],
    });
    const unfilteredHit = fakePublicWatchlistHit(1, 12);
    mocks.tsSearch.mockResolvedValueOnce({ hits: [filteredHit, unfilteredHit], found: 2 });
    mocks.dbExecute.mockResolvedValueOnce([
      { watchlist_id: "wl-0", company_ids: ["company-a", "company-b"] },
    ]);
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 14, hits: [] }] });

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(result.watchlists.map((w) => w.activeJobCount)).toEqual([14, 12]);
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).toHaveBeenCalledTimes(1);
    const searches = mocks.tsMultiSearch.mock.calls[0]?.[0].searches;
    expect(searches).toHaveLength(1);
    expect(searches[0].filter_by).toContain("company_id:[company-a,company-b]");
    expect(searches[0].filter_by).toContain("location_ids:[2658434]");
    expect(searches[0].filter_by).toContain("occupation_ids:[1]");
    expect(searches[0].filter_by).toContain("locales:[en,_none]");
  });

  it("fires no per-row count when no anyCompany rows are present", async () => {
    const hits = [
      fakePublicWatchlistHit(0, 50),
      fakePublicWatchlistHit(1, 80),
    ];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 2 });

    await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    // Only the watchlist search — no job_posting count fan-out.
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).not.toHaveBeenCalled();
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("degrades to the Typesense doc count when filters_json is missing", async () => {
    const anyHit = fakePublicWatchlistHit(0, 0);
    mocks.tsSearch.mockResolvedValueOnce({ hits: [anyHit], found: 1 });

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    // Without indexed filters we cannot detect `anyCompany`, so the row
    // falls through to the Typesense doc's denormalized 0.
    // No job_posting count fires.
    expect(result.watchlists).toHaveLength(1);
    expect(result.watchlists[0].activeJobCount).toBe(0);
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).not.toHaveBeenCalled();
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("does not broaden counts when taxonomy slugs lack resolved IDs", async () => {
    const anyHit = fakePublicWatchlistHit(0, 0, {
      anyCompany: true,
      locationSlugs: ["switzerland"],
    });
    mocks.tsSearch.mockResolvedValueOnce({ hits: [anyHit], found: 1 });

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(result.watchlists[0].activeJobCount).toBe(0);
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).not.toHaveBeenCalled();
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
  });

  it("passes viewer languages through to the anyCompany Typesense count", async () => {
    const anyHit = fakePublicWatchlistHit(0, 0, { anyCompany: true });
    mocks.tsSearch.mockResolvedValueOnce({ hits: [anyHit], found: 1 });
    mocks.getViewerLanguages.mockResolvedValueOnce(["de"]);
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 4242, hits: [] }] });

    const buildFilterStringMock = vi.mocked(
      await import("@/lib/search/typesense-filters"),
    ).buildFilterString;
    buildFilterStringMock.mockClear();

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "de" });

    expect(mocks.getViewerLanguages).toHaveBeenCalledWith("de");
    // The viewer's languages reach `buildFilterString` so the tile
    // count's filter shape matches the watchlist-detail page (#3344).
    const lastCall = buildFilterStringMock.mock.calls.at(-1);
    expect(lastCall?.[0]).toMatchObject({ languages: ["de"] });
    expect(result.watchlists[0].activeJobCount).toBe(4242);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });
});

describe("searchPublicWatchlists — anyCompany count patch (#3352)", () => {
  it("patches anyCompany rows with a live Typesense count", async () => {
    const anyHit = fakePublicWatchlistHit(0, 0, { anyCompany: true });
    const normalHit = fakePublicWatchlistHit(1, 42);
    mocks.tsSearch.mockResolvedValueOnce({ hits: [anyHit, normalHit], found: 2 });
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 1234, hits: [] }] });

    const result = await searchPublicWatchlists({
      query: "python",
      offset: 0,
      limit: 20,
      locale: "en",
    });

    expect(result.watchlists).toHaveLength(2);
    expect(result.watchlists[0].activeJobCount).toBe(1234);
    expect(result.watchlists[1].activeJobCount).toBe(42);

    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("patches filtered company-scoped rows with one multi_search (#3261)", async () => {
    const filteredHit = fakePublicWatchlistHit(0, 250, {
      keywords: ["python"],
      locationSlugs: ["zurich"],
      locationIds: [2657896],
    });
    const unfilteredHit = fakePublicWatchlistHit(1, 42);
    mocks.tsSearch.mockResolvedValueOnce({ hits: [filteredHit, unfilteredHit], found: 2 });
    mocks.dbExecute.mockResolvedValueOnce([
      { watchlist_id: "wl-0", company_ids: ["company-a"] },
    ]);
    mocks.tsMultiSearch.mockResolvedValueOnce({ results: [{ found: 8, hits: [] }] });

    const result = await searchPublicWatchlists({
      query: "python",
      offset: 0,
      limit: 20,
      locale: "en",
    });

    expect(result.watchlists.map((w) => w.activeJobCount)).toEqual([8, 42]);
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).toHaveBeenCalledTimes(1);
    const searches = mocks.tsMultiSearch.mock.calls[0]?.[0].searches;
    expect(searches).toHaveLength(1);
    expect(searches[0]).toMatchObject({
      collection: "job_posting",
      q: "python",
      query_by: "title",
      per_page: 0,
    });
    expect(searches[0].filter_by).toContain("company_id:[company-a]");
    expect(searches[0].filter_by).toContain("location_ids:[2657896]");
  });

  it("fires no per-row count when no anyCompany rows are present", async () => {
    const hits = [fakePublicWatchlistHit(0, 10), fakePublicWatchlistHit(1, 20)];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 2 });

    await searchPublicWatchlists({ query: "python", offset: 0, limit: 20, locale: "en" });

    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsMultiSearch).not.toHaveBeenCalled();
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("returns 0 when the per-row anyCompany count fails", async () => {
    const anyHit = fakePublicWatchlistHit(0, 0, { anyCompany: true });
    mocks.tsSearch.mockResolvedValueOnce({ hits: [anyHit], found: 1 });
    mocks.tsMultiSearch.mockRejectedValueOnce(new Error("typesense unreachable"));

    const result = await searchPublicWatchlists({
      query: "python",
      offset: 0,
      limit: 20,
      locale: "en",
    });

    expect(result.watchlists).toHaveLength(1);
    expect(result.watchlists[0].activeJobCount).toBe(0); // graceful degradation
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });
});
