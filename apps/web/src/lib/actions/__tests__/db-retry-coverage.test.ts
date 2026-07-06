import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Issue #2930: extends `withDbRetry` (#2929 / #2918) to additional
 * build-critical and runtime-critical `db.execute` call sites across
 * `lib/actions/*` and `lib/sitemap.ts`.
 *
 * The retry helper itself has 18 dedicated unit tests in
 * `apps/web/src/lib/__tests__/db-retry.test.ts` covering retry
 * semantics (backoff, jitter, code/message matching, `cause`-recursion,
 * non-retryable propagation). This file is a small integration-style
 * check that the wrapper is actually wired through on representative
 * call sites — i.e. that a transient ECONNRESET from `db.execute`
 * is retried (rather than rethrown) and the second attempt's success
 * surfaces back to the caller.
 *
 * One spec per representative module:
 *   - `_fetchPublicWatchlistByUserAndSlug` — build-critical (blog
 *     mention prerender + watchlist OG + watchlist page metadata).
 *   - `fetchSitemapWatchlists` (via `cachedSitemapWatchlists`) —
 *     build-critical (the route handler runs at every sitemap fetch).
 *   - `getCurrencyRates` (via `_fetchCurrencyRates`) — runtime-hot;
 *     used by /explore, /company/<slug>, settings, and salary modal.
 *   - `getCompanyTopLocations` (via `_fetchTopLocations`) — explicitly
 *     called out in the #2918 follow-up bullet list.
 *
 * Each spec mocks `db.execute` to throw an ECONNRESET on the first
 * call and resolve normally on the second. Asserting `toHaveBeenCalledTimes(2)`
 * proves that the wrapper retried — without the wrapper, the first
 * rejection would propagate out untouched.
 */

vi.mock("server-only", () => ({}));

const dbExecuteMock = vi.fn();
const cachedMock = vi.fn(
  async (_key: string, fetcher: () => Promise<unknown>) => fetcher(),
);

vi.mock("@/db", () => ({
  db: {
    execute: dbExecuteMock,
    select: vi.fn(() => ({
      from: vi.fn(() => ({
        innerJoin: vi.fn(() => ({
          where: vi.fn(() => ({
            orderBy: vi.fn().mockResolvedValue([]),
          })),
        })),
      })),
    })),
  },
}));

vi.mock("@/lib/cache", () => ({
  cached: cachedMock,
  invalidate: vi.fn(),
  invalidatePattern: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: vi.fn().mockResolvedValue(null),
}));

vi.mock("@/lib/viewer", () => ({
  getViewerLanguages: vi.fn().mockResolvedValue(["en"]),
}));

// `next/server` and `next/cache` aren't reachable in the vitest runtime
// — the modules under test pull them in transitively via Next's server
// helpers, so neutralise them.
vi.mock("next/server", () => ({ after: vi.fn() }));
vi.mock("next/cache", () => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  updateTag: vi.fn(),
}));

// Search clients aren't in scope; every call site under test reaches
// Postgres directly (or through the helper).
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: vi.fn(() => ({
    collections: () => ({
      documents: () => ({
        search: vi.fn().mockRejectedValue(new Error("typesense unreachable")),
      }),
    }),
  })),
  getTypesenseClient: vi.fn(() => ({
    collections: () => ({
      documents: () => ({
        search: vi.fn().mockRejectedValue(new Error("typesense unreachable")),
      }),
    }),
  })),
}));

vi.mock("@/lib/search/typesense-watchlist", () => ({
  upsertWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  updateWatchlistField: vi.fn(),
}));

vi.mock("@/lib/indexnow", () => ({
  notifyIndexNow: vi.fn().mockResolvedValue({ kind: "submitted", status: 200 }),
}));

vi.mock("@/lib/db-retry", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/db-retry")
  >("@/lib/db-retry");
  return {
    ...actual,
    // Override the `withDbRetry` default sleep so the suite doesn't
    // wait 200ms+ between retries. We import the real implementation
    // (so the policy stays under test) and only collapse the timer.
    withDbRetry: <T,>(
      fn: () => Promise<T>,
      opts: Parameters<typeof actual.withDbRetry>[1] = {},
    ) =>
      actual.withDbRetry(fn, {
        ...opts,
        sleep: () => Promise.resolve(),
      }),
  };
});

const econnreset = (): Error => {
  const e = new Error("read ECONNRESET") as Error & { code: string };
  e.code = "ECONNRESET";
  return e;
};

const ORIGINAL_DATABASE_URL = process.env.DATABASE_URL;

describe("issue #2930 — withDbRetry covers additional call sites", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    cachedMock.mockClear();
    process.env.DATABASE_URL =
      ORIGINAL_DATABASE_URL ?? "postgresql://test:test@localhost:5432/test";
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (ORIGINAL_DATABASE_URL === undefined) {
      delete process.env.DATABASE_URL;
    } else {
      process.env.DATABASE_URL = ORIGINAL_DATABASE_URL;
    }
  });

  it("retries _fetchPublicWatchlistByUserAndSlug after ECONNRESET", async () => {
    // First execute() rejects (transient pooler reset), second
    // execute() returns the watchlist row.
    dbExecuteMock.mockRejectedValueOnce(econnreset()).mockResolvedValueOnce([
      {
        wl_id: "wl-1",
        slug: "remote-frontend",
        title: "Remote Frontend",
        description: null,
        is_public: true,
        alerts_enabled: false,
        filters: {},
        source_watchlist_id: null,
        created_at: new Date(),
        user_id: "u-1",
        owner_id: "u-1",
        username: "alice",
        display_username: "alice",
        owner_name: "Alice",
      },
    ]);

    const { getPublicWatchlistByUserAndSlug } = await import(
      "@/lib/actions/watchlists"
    );
    const result = await getPublicWatchlistByUserAndSlug(
      "alice",
      "remote-frontend",
    );

    expect(result?.slug).toBe("remote-frontend");
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
  });

  it("retries cachedSitemapWatchlists after ECONNRESET", async () => {
    dbExecuteMock
      .mockRejectedValueOnce(econnreset())
      .mockResolvedValueOnce([
        {
          user_slug: "alice",
          watchlist_slug: "remote-frontend",
          updated_at: new Date(),
          is_curated: false,
        },
      ]);

    const { cachedSitemapWatchlists } = await import("@/lib/sitemap");
    const rows = await cachedSitemapWatchlists();

    expect(rows.length).toBe(1);
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
  });

  it("retries getCurrencyRates after ECONNRESET", async () => {
    dbExecuteMock
      .mockRejectedValueOnce(econnreset())
      .mockResolvedValueOnce([
        { currency: "EUR", to_eur: "1.0" },
        { currency: "USD", to_eur: "0.92" },
      ]);

    const { getCurrencyRates } = await import("@/lib/actions/search");
    const rates = await getCurrencyRates();

    expect(rates.find((r) => r.currency === "USD")?.toEur).toBeCloseTo(0.92);
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
  });

  it("retries getCompanyTopLocations after ECONNRESET", async () => {
    dbExecuteMock
      .mockRejectedValueOnce(econnreset())
      .mockResolvedValueOnce([
        {
          location_id: 1,
          loc_slug: "berlin",
          loc_type: "city",
          loc_name: "Berlin",
          cnt: 5,
          total_locations: 1,
        },
      ]);

    const { getCompanyTopLocations } = await import("@/lib/actions/company");
    const result = await getCompanyTopLocations("c-1", "en");

    expect(result.locations).toHaveLength(1);
    expect(result.locations[0]?.slug).toBe("berlin");
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
  });

  it("propagates non-retryable errors (syntax) without retrying", async () => {
    // Sanity check: the wrapper should not retry on errors that aren't
    // connection-class. Pick any wrapped site.
    const syntaxErr = new Error('syntax error at or near "FROM"');
    dbExecuteMock.mockRejectedValue(syntaxErr);

    const { getCurrencyRates } = await import("@/lib/actions/search");

    // `getCurrencyRates` outer try/catch swallows errors and returns the
    // EUR fallback shape. Assert we got the fallback AND `db.execute`
    // was called exactly once (no retry).
    const rates = await getCurrencyRates();
    expect(rates).toEqual([{ currency: "EUR", toEur: 1 }]);
    expect(dbExecuteMock).toHaveBeenCalledTimes(1);
  });
});
