/**
 * Perf regression test (issue #3211).
 *
 * Asserts that the watchlist-detail loaders — `getWatchlistByUserAndSlug`
 * and `getPublicWatchlistByUserAndSlug` — make **exactly one** Postgres
 * round-trip per call, folding the previously-separate
 * `watchlist_company JOIN company` lookup into the main watchlist+user
 * query via a `json_agg` subquery.
 *
 * Pre-fix:
 *   1. `db.execute(sql\`SELECT … FROM watchlist JOIN "user" …\`)` —
 *      resolves the watchlist row.
 *   2. `db.select().from(watchlist_company).innerJoin(company)…` —
 *      a second sequential round-trip to load the companies array.
 *
 * Each query was ~5–15ms on a cold pool, so the serial round-trip wasted
 * ~10–30ms per render. Worse, the watchlist page issues this call from
 * BOTH `generateMetadata` AND the page body — every visit took 4 sequential
 * SQL queries instead of 2 (the Redis cache around
 * `getPublicWatchlistByUserAndSlug` shares them within a hot cache, but
 * the cold path still does it).
 *
 * Post-fix:
 *   Single `db.execute` call that returns the watchlist row PLUS the
 *   companies array via a `COALESCE(json_agg(...), '[]'::json)`
 *   correlated subquery, mirroring the same pattern already used by
 *   `getUserWatchlists` for its denormalized counts.
 *
 * These tests pin both:
 *   - call count (regression guard: 1, not 2)
 *   - returned shape (functional guard: same fields callers consume —
 *     `id, slug, title, …, owner.{id,username,displayUsername,name},
 *     companies[].{id,name,slug,icon}`)
 *   - empty-companies edge case (`COALESCE(json_agg(...), '[]')` must
 *     hand the caller `[]`, not `null` or `undefined`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  dbExecute: vi.fn(),
  // Track every drizzle-builder `select(...)` invocation so the test can
  // assert that the legacy second query is GONE. Production code paths
  // that still use `db.select()` (e.g. cache-touch helpers) should not
  // fire from these loaders — the read path is now pure raw-SQL.
  dbSelect: vi.fn(),
  // `update(...)`/`delete(...)` chains are still allowed (the owner
  // path fires `db.update(watchlist).set({ lastAccessedAt: … })` as a
  // detached side-effect). The test ignores them; the assertion is on
  // `dbExecute` round-trips, which is the user-visible serial latency.
  dbUpdate: vi.fn(),

  getSessionUserId: vi.fn(),
  cached: vi.fn(),
  withDbRetry: vi.fn(),
}));

vi.mock("next/server", () => ({ after: (cb: () => unknown) => cb() }));
vi.mock("next/cache", () => ({ updateTag: vi.fn() }));

vi.mock("@/lib/cache", () => ({
  // `cached(key, factory, opts)` — pass-through to the factory so the
  // public loader actually runs.
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
  getViewerLanguages: vi.fn().mockResolvedValue(["en"]),
}));

vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: vi.fn().mockResolvedValue({ allowed: true }),
  getUserPlan: vi.fn().mockResolvedValue("free"),
  PLAN_LIMITS: { free: { canReceiveAlerts: false }, paid: { canReceiveAlerts: true } },
}));

vi.mock("@/lib/indexnow", () => ({
  notifyIndexNow: vi.fn().mockResolvedValue({ kind: "submitted", status: 200, urlCount: 0 }),
}));

vi.mock("@/lib/search/typesense-watchlist", () => ({
  upsertWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  updateWatchlistField: vi.fn(),
}));

vi.mock("@/lib/watchlist-slug", () => ({
  generateUniqueSlug: vi.fn(),
  insertWatchlistWithUniqueSlug: vi.fn(),
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: vi.fn() }) }),
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

// Drizzle builder mock for `select`/`update`. Each call increments the
// corresponding spy; the chain is a thenable that resolves with [] so
// any straggler that still uses `db.select()` doesn't crash the test.
function makeSelectChain() {
  const chain: Record<string, unknown> = {};
  for (const m of ["from", "innerJoin", "leftJoin", "where", "orderBy", "limit"]) {
    chain[m] = () => chain;
  }
  chain.then = (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) =>
    Promise.resolve([]).then(resolve, reject);
  return chain;
}

function makeUpdateChain() {
  const chain: Record<string, unknown> = {};
  for (const m of ["set", "where"]) {
    chain[m] = () => chain;
  }
  chain.then = (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) =>
    Promise.resolve(undefined).then(resolve, reject);
  // Drizzle's update chain also supports `.catch(...)` for the
  // fire-and-forget owner-touch — the production code path uses
  // `.catch(() => {})` directly on the chain (not after `await`).
  chain.catch = () => Promise.resolve(undefined);
  return chain;
}

vi.mock("@/db", () => ({
  db: {
    execute: (...args: unknown[]) => mocks.dbExecute(...args),
    select: (...args: unknown[]) => {
      mocks.dbSelect(...args);
      return makeSelectChain();
    },
    update: (...args: unknown[]) => {
      mocks.dbUpdate(...args);
      return makeUpdateChain();
    },
  },
}));

// Module under test — must come AFTER the vi.mock factories.
import {
  getWatchlistByUserAndSlug,
  getPublicWatchlistByUserAndSlug,
} from "../watchlists";

const USER_ID = "user-1";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getSessionUserId.mockResolvedValue(USER_ID);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- helpers ----------------------------------------------------------

function fakeWatchlistRow(opts?: {
  isPublic?: boolean;
  userId?: string;
  companies?: { id: string; name: string; slug: string; icon: string | null }[];
}) {
  const companies = opts?.companies ?? [
    { id: "co-1", name: "Acme", slug: "acme", icon: "acme.png" },
    { id: "co-2", name: "Globex", slug: "globex", icon: null },
  ];
  return {
    wl_id: "wl-1",
    slug: "my-watchlist",
    title: "My Watchlist",
    description: "A test watchlist",
    is_public: opts?.isPublic ?? true,
    alerts_enabled: false,
    filters: { keywords: ["python"] },
    source_watchlist_id: null,
    created_at: new Date("2026-05-01T00:00:00Z"),
    user_id: opts?.userId ?? USER_ID,
    owner_id: opts?.userId ?? USER_ID,
    username: "alice",
    display_username: "Alice",
    owner_name: "Alice Cooper",
    companies,
  };
}

// ---- getWatchlistByUserAndSlug (session-aware) -------------------------

describe("getWatchlistByUserAndSlug — single-query fold (#3211)", () => {
  it("issues exactly ONE db.execute round-trip (not 2)", async () => {
    mocks.dbExecute.mockResolvedValueOnce([fakeWatchlistRow()]);

    await getWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    // Belt-and-suspenders: the legacy second query used the drizzle
    // builder (`db.select().from(watchlistCompany).innerJoin(company)`).
    // Asserting that `db.select` is NOT touched pins that the companies
    // array now arrives via the same `db.execute` round-trip.
    expect(mocks.dbSelect).not.toHaveBeenCalled();
  });

  it("returns the full WatchlistDetail shape callers consume", async () => {
    mocks.dbExecute.mockResolvedValueOnce([
      fakeWatchlistRow({
        companies: [
          { id: "co-1", name: "Acme", slug: "acme", icon: "acme.png" },
          { id: "co-2", name: "Globex", slug: "globex", icon: null },
        ],
      }),
    ]);

    const detail = await getWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(detail).not.toBeNull();
    expect(detail).toMatchObject({
      id: "wl-1",
      slug: "my-watchlist",
      title: "My Watchlist",
      description: "A test watchlist",
      isPublic: true,
      alertsEnabled: false,
      filters: { keywords: ["python"] },
      sourceWatchlistId: null,
      owner: {
        id: USER_ID,
        username: "alice",
        displayUsername: "Alice",
        name: "Alice Cooper",
      },
      companies: [
        { id: "co-1", name: "Acme", slug: "acme", icon: "acme.png" },
        { id: "co-2", name: "Globex", slug: "globex", icon: null },
      ],
    });
    expect(detail!.createdAt).toBe("2026-05-01T00:00:00.000Z");
  });

  it("returns companies=[] when the watchlist has no companies (COALESCE-null guard)", async () => {
    // The SQL must return an empty array, never `null`, otherwise
    // consumers like `detail.companies.length`, `detail.companies.map(...)`
    // would crash. The Postgres `json_agg` returns NULL on zero rows,
    // so the query must wrap it in `COALESCE(..., '[]'::json)`.
    mocks.dbExecute.mockResolvedValueOnce([
      fakeWatchlistRow({ companies: [] }),
    ]);

    const detail = await getWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(detail).not.toBeNull();
    expect(detail!.companies).toEqual([]);
    // Critical: consumers iterate this array unconditionally
    // (page.tsx line ~80: `detail.companies.length`,
    //  opengraph-image.tsx: `detail.companies.length`,
    //  watchlist-page-data.ts: `detail.companies.map(c => c.id)`).
    expect(Array.isArray(detail!.companies)).toBe(true);
  });

  it("returns null for a private watchlist owned by someone else", async () => {
    mocks.dbExecute.mockResolvedValueOnce([
      fakeWatchlistRow({ isPublic: false, userId: "other-user" }),
    ]);

    const detail = await getWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(detail).toBeNull();
    // The single SELECT already ran (we need it to discover ownership).
    // The legacy code path issued the companies SELECT next; the new
    // path returns immediately after the access check.
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(mocks.dbSelect).not.toHaveBeenCalled();
  });

  it("returns null when no row matches", async () => {
    mocks.dbExecute.mockResolvedValueOnce([]);

    const detail = await getWatchlistByUserAndSlug("ghost", "ghost-list");

    expect(detail).toBeNull();
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(mocks.dbSelect).not.toHaveBeenCalled();
  });
});

// ---- getPublicWatchlistByUserAndSlug (no session) ----------------------

describe("getPublicWatchlistByUserAndSlug — single-query fold (#3211)", () => {
  it("issues exactly ONE db.execute round-trip (not 2)", async () => {
    mocks.dbExecute.mockResolvedValueOnce([fakeWatchlistRow()]);

    await getPublicWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(mocks.dbSelect).not.toHaveBeenCalled();
  });

  it("returns the full WatchlistDetail shape callers consume", async () => {
    mocks.dbExecute.mockResolvedValueOnce([fakeWatchlistRow()]);

    const detail = await getPublicWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(detail).not.toBeNull();
    expect(detail).toMatchObject({
      id: "wl-1",
      slug: "my-watchlist",
      isPublic: true,
      owner: {
        id: USER_ID,
        username: "alice",
        displayUsername: "Alice",
        name: "Alice Cooper",
      },
      companies: [
        { id: "co-1", name: "Acme", slug: "acme", icon: "acme.png" },
        { id: "co-2", name: "Globex", slug: "globex", icon: null },
      ],
    });
  });

  it("returns companies=[] when the watchlist has no companies (COALESCE-null guard)", async () => {
    mocks.dbExecute.mockResolvedValueOnce([
      fakeWatchlistRow({ companies: [] }),
    ]);

    const detail = await getPublicWatchlistByUserAndSlug("alice", "my-watchlist");

    expect(detail).not.toBeNull();
    expect(detail!.companies).toEqual([]);
    expect(Array.isArray(detail!.companies)).toBe(true);
  });

  it("returns null when no row matches", async () => {
    mocks.dbExecute.mockResolvedValueOnce([]);

    const detail = await getPublicWatchlistByUserAndSlug("ghost", "ghost-list");

    expect(detail).toBeNull();
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(mocks.dbSelect).not.toHaveBeenCalled();
  });
});
