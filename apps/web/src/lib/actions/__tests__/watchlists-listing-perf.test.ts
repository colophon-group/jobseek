/**
 * Perf regression test (issue #3176).
 *
 * Asserts that the watchlist listing surfaces — `getUserWatchlists`,
 * `getPopularWatchlists`, and `searchPublicWatchlists` — do NOT fan out
 * per-watchlist Typesense `job_posting` count queries.
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
 * Post-fix:
 *   - `getUserWatchlists`  one SQL query against Postgres with the
 *     active count denormalized via a `watchlist_company JOIN
 *     job_posting WHERE is_active` subquery. Zero Typesense round-trips.
 *   - `searchPublicWatchlists` / `getPopularWatchlists`  one Typesense
 *     `watchlist` collection search whose returned docs already carry
 *     `active_job_count` (refreshed every 4h by the crawler's
 *     `refresh-typesense` job). Zero additional Typesense round-trips.
 *
 * The trade-off: the listing count ignores the per-watchlist filters
 * (keywords, locations, work_mode, …) and the viewer's language
 * preference. The watchlist-detail page still surfaces the precise
 * filter-applied count via `getWatchlistPostingDisplayCounts`. The
 * follow-up issue (#3261) tracks restoring per-viewer / per-filter
 * accuracy via a batched `multi_search` if the UX warrants it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getSessionUserId: vi.fn(),
  dbExecute: vi.fn(),
  withDbRetry: vi.fn(),
  cached: vi.fn(),

  // Typesense search call counter — every collection().documents().search()
  // routes through this one mock so we can assert call count + which
  // collection was hit.
  tsSearch: vi.fn(),
  tsCollectionsCalls: [] as string[],

  getViewerLanguages: vi.fn().mockResolvedValue(["en"]),
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
}));

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
  buildFilterString: vi.fn(() => ""),
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
  resolveLocationSlugs: vi.fn().mockResolvedValue(new Map()),
}));

vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn().mockResolvedValue([]),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveOccupationSlugs: vi.fn().mockResolvedValue(new Map()),
  resolveSenioritySlugs: vi.fn().mockResolvedValue(new Map()),
  resolveTechnologySlugs: vi.fn().mockResolvedValue(new Map()),
}));

vi.mock("drizzle-orm", () => ({
  sql: (..._args: unknown[]) => ({ _isSql: true }),
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
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- helpers ----------------------------------------------------------

function fakeUserWatchlistRow(i: number, activeJobCount: number) {
  return {
    id: `wl-${i}`,
    slug: `slug-${i}`,
    title: `Watchlist ${i}`,
    description: null,
    is_public: i % 2 === 0,
    alerts_enabled: false,
    filters: {
      keywords: ["python", "remote"],
      locationSlugs: ["zurich"],
      workMode: ["remote"],
    },
    last_accessed_at: new Date("2026-05-14T00:00:00Z"),
    created_at: new Date("2026-05-01T00:00:00Z"),
    company_count: 3,
    active_job_count: activeJobCount,
  };
}

function fakePublicWatchlistHit(i: number, activeJobCount: number) {
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
    mocks.tsSearch.mockResolvedValueOnce({ found: 38717, hits: [] });

    const result = await getUserWatchlists("en");

    expect(result).toHaveLength(2);
    // Patched from SQL's 0 to the Typesense count.
    expect(result[0].activeJobCount).toBe(38717);
    // Non-anyCompany row keeps the SQL count untouched — no extra
    // round-trip fires for it.
    expect(result[1].activeJobCount).toBe(12);

    // Exactly one Typesense call (the anyCompany count). The normal
    // row goes straight from SQL.
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual(["job_posting"]);
  });

  it("returns 0 (not the SQL 0 either) when the anyCompany Typesense count fails", async () => {
    const anyRow = {
      ...fakeUserWatchlistRow(0, 0),
      filters: { anyCompany: true, locationSlugs: ["eu"] },
    };
    mocks.dbExecute.mockResolvedValueOnce([anyRow]);
    mocks.tsSearch.mockRejectedValueOnce(new Error("typesense unreachable"));

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
});

// ---- public Discover surfaces (Typesense path) -------------------------

describe("getPopularWatchlists — Typesense path (#3176)", () => {
  it("issues exactly ONE Typesense query (watchlist collection) — no per-row job_posting fan-out", async () => {
    const N = 20;
    const hits = Array.from({ length: N }, (_, i) => fakePublicWatchlistHit(i, i * 5));
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: N });

    await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.tsCollectionsCalls).toEqual(["watchlist"]);
  });

  it("preserves the denormalized active_job_count from the Typesense doc", async () => {
    const hits = [
      fakePublicWatchlistHit(0, 100),
      fakePublicWatchlistHit(1, 50),
      fakePublicWatchlistHit(2, 25),
    ];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 3 });

    const result = await getPopularWatchlists({ offset: 0, limit: 20, locale: "en" });

    expect(result.watchlists.map((w) => w.activeJobCount)).toEqual([100, 50, 25]);
  });

  it("does NOT touch Postgres on the Typesense success path", async () => {
    const hits = [fakePublicWatchlistHit(0, 10)];
    mocks.tsSearch.mockResolvedValueOnce({ hits, found: 1 });

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
  });

  it("preserves the denormalized active_job_count from the Typesense doc", async () => {
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
