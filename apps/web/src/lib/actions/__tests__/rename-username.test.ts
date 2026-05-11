import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Server actions transitively import `server-only`, which throws in a
// non-Next runtime. Neutralize before module-under-test loads.
vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  // next/cache
  revalidatePath: vi.fn(),
  cacheLife: vi.fn(),
  updateTag: vi.fn(),
  // next/server after() — invoke the callback synchronously so the test
  // can assert IndexNow side effects without yielding to a scheduler.
  after: vi.fn(<T,>(cb: () => T) => cb()),
  // session cache
  getSession: vi.fn(),
  getSessionUserId: vi.fn(),
  invalidateAllUserSessionCacheEntries: vi.fn().mockResolvedValue(0),
  // redis cache layer
  invalidateRedis: vi.fn().mockResolvedValue(undefined),
  // typesense
  tsUpdateWatchlistField: vi.fn(),
  // indexnow
  notifyIndexNow: vi.fn().mockResolvedValue(undefined),
  // watchlist-utils — explicit signature so mockImplementation accepts
  // a (filters, companyCount) predicate in the "trivial subset" test.
  isTrivialWatchlist: vi.fn<(filters: unknown, companyCount: number) => boolean>(
    () => false,
  ),
  // auth
  updateUser: vi.fn().mockResolvedValue({ status: true }),
  setPassword: vi.fn(),
  // anon prefs (loaded by preferences module graph)
  writeAnonJobLanguagesCookie: vi.fn(),
  readAnonJobLanguagesCookie: vi.fn(),
  // Drizzle: `db.select(...).from(user).where().limit(1)` is the OLD
  // user-row lookup; `db.execute(sql\`SELECT ... FROM watchlist ...\`)`
  // is the watchlist snapshot. Tests queue them in this order.
  selectQueue: [] as unknown[],
  executeQueue: [] as unknown[],
}));

vi.mock("next/cache", () => ({
  revalidatePath: mocks.revalidatePath,
  cacheLife: mocks.cacheLife,
  updateTag: mocks.updateTag,
}));

vi.mock("next/server", () => ({
  after: mocks.after,
}));

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSession: mocks.getSession,
  getSessionUserId: mocks.getSessionUserId,
  invalidateAllUserSessionCacheEntries: mocks.invalidateAllUserSessionCacheEntries,
}));

vi.mock("@/lib/cache", () => ({
  invalidate: mocks.invalidateRedis,
}));

vi.mock("@/lib/cache-tags", () => ({
  // Stable, predictable tag format so assertions can match precisely.
  watchlistCacheTag: (userSlug: string, watchlistSlug: string) =>
    `watchlist:${userSlug}:${watchlistSlug}`,
}));

vi.mock("@/lib/search/typesense-watchlist", () => ({
  updateWatchlistField: mocks.tsUpdateWatchlistField,
}));

vi.mock("@/lib/watchlist-utils", () => ({
  isTrivialWatchlist: mocks.isTrivialWatchlist,
}));

vi.mock("@/lib/indexnow", () => ({
  notifyIndexNow: mocks.notifyIndexNow,
}));

vi.mock("@/lib/anon-preferences", () => ({
  writeAnonJobLanguagesCookie: mocks.writeAnonJobLanguagesCookie,
  readAnonJobLanguagesCookie: mocks.readAnonJobLanguagesCookie,
}));

// preferences.ts also imports getSearchClient at module-load time via
// `getAvailableJobLanguages`. Stub it so the import graph resolves.
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      updateUser: mocks.updateUser,
      setPassword: mocks.setPassword,
    },
  },
}));

// Minimal Drizzle stub. Each select() returns a chain whose `where()` is
// thenable AND has `.limit()` — supports both
// `select().from().where()` (the watchlists read) and
// `select().from().where().limit(1)` (the user read).
const buildSelectChain = () => ({
  from: () => ({
    where: () => {
      const result = mocks.selectQueue.shift() ?? [];
      const p: Promise<unknown> & { limit?: () => Promise<unknown> } =
        Promise.resolve(result);
      p.limit = () => Promise.resolve(result);
      return p;
    },
  }),
});

vi.mock("@/db", () => ({
  db: {
    select: () => buildSelectChain(),
    execute: () =>
      Promise.resolve(mocks.executeQueue.shift() ?? []),
  },
}));

describe("renameUsername", () => {
  beforeEach(() => {
    mocks.revalidatePath.mockReset();
    mocks.updateTag.mockReset();
    mocks.after.mockReset().mockImplementation(<T,>(cb: () => T) => cb());
    mocks.getSession.mockReset();
    mocks.invalidateAllUserSessionCacheEntries.mockReset().mockResolvedValue(0);
    mocks.invalidateRedis.mockReset().mockResolvedValue(undefined);
    mocks.tsUpdateWatchlistField.mockReset();
    mocks.notifyIndexNow.mockReset().mockResolvedValue(undefined);
    mocks.isTrivialWatchlist.mockReset().mockReturnValue(false);
    mocks.updateUser.mockReset().mockResolvedValue({ status: true });
    mocks.selectQueue.length = 0;
    mocks.executeQueue.length = 0;
  });

  afterEach(() => {
    vi.resetModules();
  });

  it("returns error when not authenticated", async () => {
    mocks.getSession.mockResolvedValue(null);
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("newname");

    expect(result).toEqual({ error: "Not authenticated" });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("validates length", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    const { renameUsername } = await import("../preferences");

    expect(await renameUsername("ab")).toEqual({
      error: "Username must be 3-30 characters",
    });
    expect(
      await renameUsername("a".repeat(31)),
    ).toEqual({ error: "Username must be 3-30 characters" });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("validates character set after lowercase-normalization", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    const { renameUsername } = await import("../preferences");

    // Hyphen at the start / end is rejected by the regex (matches the
    // client-side check in `UsernameSection`).
    expect(await renameUsername("-leading-hyphen")).toEqual({
      error: "Username has invalid characters",
    });
    expect(await renameUsername("trailing-")).toEqual({
      error: "Username has invalid characters",
    });
    // Special chars that survive normalization but aren't [a-z0-9-].
    expect(await renameUsername("has space")).toEqual({
      error: "Username has invalid characters",
    });
    expect(await renameUsername("with.dot")).toEqual({
      error: "Username has invalid characters",
    });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("rejects reserved usernames", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    const { renameUsername } = await import("../preferences");

    expect(await renameUsername("admin")).toEqual({
      error: "Username is reserved",
    });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("no-ops when the new name already matches the current one", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    mocks.selectQueue.push([
      { username: "samename", displayUsername: "samename" },
    ]);
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("samename");

    expect(result).toEqual({});
    expect(mocks.updateUser).not.toHaveBeenCalled();
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateAllUserSessionCacheEntries).not.toHaveBeenCalled();
  });

  it("fans out cache invalidations against the OLD slug for each watchlist", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.selectQueue.push([
      { username: "old", displayUsername: "old" }, // user row
    ]);
    mocks.executeQueue.push([
      {
        id: "wl-a",
        slug: "alpha",
        is_public: true,
        filters: { keywords: ["k1", "k2"] },
        company_count: 5,
      },
      {
        id: "wl-b",
        slug: "beta",
        is_public: true,
        filters: { keywords: ["k1", "k2"] },
        company_count: 5,
      },
    ]);
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("new");
    expect(result).toEqual({});

    // Better Auth was asked to do the actual rename.
    expect(mocks.updateUser).toHaveBeenCalledTimes(1);
    expect(mocks.updateUser).toHaveBeenCalledWith(
      expect.objectContaining({ body: { username: "new" } }),
    );

    // updateTag was called for the OLD user slug + each watchlist slug.
    expect(mocks.updateTag).toHaveBeenCalledWith("watchlist:old:alpha");
    expect(mocks.updateTag).toHaveBeenCalledWith("watchlist:old:beta");

    // Redis public-watchlist:OLD:slug invalidations.
    expect(mocks.invalidateRedis).toHaveBeenCalledWith(
      "public-watchlist:old:alpha",
    );
    expect(mocks.invalidateRedis).toHaveBeenCalledWith(
      "public-watchlist:old:beta",
    );

    // Sitemap Redis bust.
    expect(mocks.invalidateRedis).toHaveBeenCalledWith("sitemap:watchlists");

    // Typesense docs patched with NEW owner_username and is_featured
    // (derived from `normalized === "colophongroup"`, false here).
    expect(mocks.tsUpdateWatchlistField).toHaveBeenCalledWith("wl-a", {
      owner_username: "new",
      is_featured: false,
    });
    expect(mocks.tsUpdateWatchlistField).toHaveBeenCalledWith("wl-b", {
      owner_username: "new",
      is_featured: false,
    });

    // Multi-device session cache bust.
    expect(mocks.invalidateAllUserSessionCacheEntries).toHaveBeenCalledWith(
      "user-1",
    );

    // IndexNow ping (after()): new + old URLs for both qualifying watchlists.
    expect(mocks.notifyIndexNow).toHaveBeenCalledTimes(1);
    const indexNowUrls = mocks.notifyIndexNow.mock.calls[0][0] as string[];
    expect(indexNowUrls).toEqual(
      expect.arrayContaining([
        "/new/alpha",
        "/old/alpha",
        "/new/beta",
        "/old/beta",
      ]),
    );
  });

  it("refreshes Typesense is_featured when renaming TO the featured handle", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u-feat" } });
    mocks.selectQueue.push([
      { username: "old", displayUsername: null },
    ]);
    mocks.executeQueue.push([
      {
        id: "wl-1",
        slug: "s",
        is_public: false,
        filters: null,
        company_count: 0,
      },
    ]);
    const { renameUsername } = await import("../preferences");

    await renameUsername("colophongroup");

    expect(mocks.tsUpdateWatchlistField).toHaveBeenCalledWith("wl-1", {
      owner_username: "colophongroup",
      is_featured: true,
    });
  });

  it("busts BOTH username and displayUsername variants when they differ", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-2" } });
    mocks.selectQueue.push([
      { username: "old-canonical", displayUsername: "old-display" },
    ]);
    mocks.executeQueue.push([
      {
        id: "wl-x",
        slug: "x",
        is_public: false,
        filters: null,
        company_count: 0,
      },
    ]);
    const { renameUsername } = await import("../preferences");

    await renameUsername("new");

    // Both old slug variants get their cache tag busted — the route
    // resolver matches `u.username = X OR u.display_username = X`, so
    // either form could be the live URL segment.
    expect(mocks.updateTag).toHaveBeenCalledWith(
      "watchlist:old-canonical:x",
    );
    expect(mocks.updateTag).toHaveBeenCalledWith("watchlist:old-display:x");
    expect(mocks.invalidateRedis).toHaveBeenCalledWith(
      "public-watchlist:old-canonical:x",
    );
    expect(mocks.invalidateRedis).toHaveBeenCalledWith(
      "public-watchlist:old-display:x",
    );
  });

  it("normalizes input (lowercase + trim) before validation and rename", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u3" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([]);
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("  NewName  ");
    expect(result).toEqual({});
    expect(mocks.updateUser).toHaveBeenCalledWith(
      expect.objectContaining({ body: { username: "newname" } }),
    );
  });

  it("surfaces Better Auth errors and does NOT run the fanout", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u4" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([
      {
        id: "wl-1",
        slug: "s",
        is_public: true,
        filters: { keywords: ["k1", "k2"] },
        company_count: 3,
      },
    ]);
    mocks.updateUser.mockRejectedValue(new Error("Username already taken"));
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("taken");
    expect(result).toEqual({ error: "Username already taken" });
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateRedis).not.toHaveBeenCalled();
    expect(mocks.tsUpdateWatchlistField).not.toHaveBeenCalled();
    expect(mocks.invalidateAllUserSessionCacheEntries).not.toHaveBeenCalled();
    expect(mocks.notifyIndexNow).not.toHaveBeenCalled();
  });

  it("succeeds with no watchlists (still busts session + sitemap)", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u5" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([]); // no watchlists
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("new");
    expect(result).toEqual({});
    expect(mocks.updateUser).toHaveBeenCalled();
    expect(mocks.tsUpdateWatchlistField).not.toHaveBeenCalled();
    // updateTag never called for per-watchlist tags, but sitemap and
    // session cache busts still run.
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateRedis).toHaveBeenCalledWith("sitemap:watchlists");
    expect(mocks.invalidateAllUserSessionCacheEntries).toHaveBeenCalledWith(
      "u5",
    );
  });

  it("redis invalidate failure is logged but does not fail the rename", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u6" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([
      {
        id: "wl-1",
        slug: "s",
        is_public: false,
        filters: null,
        company_count: 0,
      },
    ]);
    mocks.invalidateRedis.mockRejectedValue(new Error("redis down"));
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("new");
    expect(result).toEqual({});
    expect(errSpy).toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it("skips IndexNow for private or trivial watchlists", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u-idx" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([
      // Private — skip.
      {
        id: "wl-private",
        slug: "private",
        is_public: false,
        filters: { keywords: ["a", "b"] },
        company_count: 5,
      },
      // Public but trivial (mocked predicate returns true for it) — skip.
      {
        id: "wl-trivial",
        slug: "trivial",
        is_public: true,
        filters: {},
        company_count: 0,
      },
      // Public + non-trivial — ping.
      {
        id: "wl-good",
        slug: "good",
        is_public: true,
        filters: { keywords: ["a", "b"] },
        company_count: 4,
      },
    ]);
    // Mark only the "trivial" row as trivial.
    mocks.isTrivialWatchlist.mockImplementation((_filters, companyCount) =>
      companyCount === 0,
    );
    const { renameUsername } = await import("../preferences");

    await renameUsername("new");

    expect(mocks.notifyIndexNow).toHaveBeenCalledTimes(1);
    const urls = mocks.notifyIndexNow.mock.calls[0][0] as string[];
    expect(urls).toEqual(["/new/good", "/old/good"]);
    expect(urls).not.toEqual(
      expect.arrayContaining(["/new/private", "/new/trivial"]),
    );
  });

  it("skips notifyIndexNow entirely when no qualifying watchlists exist", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u-empty" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([
      {
        id: "wl-1",
        slug: "s",
        is_public: false,
        filters: null,
        company_count: 0,
      },
    ]);
    const { renameUsername } = await import("../preferences");

    await renameUsername("new");

    expect(mocks.notifyIndexNow).not.toHaveBeenCalled();
  });

  it("calls auth.api.updateUser BEFORE any cache fanout (ordering)", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u-order" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([
      {
        id: "wl-1",
        slug: "s",
        is_public: true,
        filters: { keywords: ["a", "b"] },
        company_count: 3,
      },
    ]);
    const { renameUsername } = await import("../preferences");

    await renameUsername("new");

    // `invocationCallOrder` is monotonically increasing across all
    // mocks in the run — comparing them enforces the sequence:
    // (1) Better Auth runs FIRST (so the DB row is flipped under the
    // user's nose), then (2) the per-watchlist cache tags / Redis /
    // Typesense / IndexNow run against the snapshotted OLD slug. A
    // future refactor that reorders the fanout pre-rename would break
    // the snapshot-of-OLD-row contract and this assertion would catch
    // it.
    const updateUserOrder = mocks.updateUser.mock.invocationCallOrder[0];
    const updateTagOrder = mocks.updateTag.mock.invocationCallOrder[0];
    const sessionBustOrder =
      mocks.invalidateAllUserSessionCacheEntries.mock.invocationCallOrder[0];
    const tsUpsertOrder =
      mocks.tsUpdateWatchlistField.mock.invocationCallOrder[0];

    expect(updateUserOrder).toBeDefined();
    expect(updateTagOrder).toBeGreaterThan(updateUserOrder);
    expect(tsUpsertOrder).toBeGreaterThan(updateUserOrder);
    expect(sessionBustOrder).toBeGreaterThan(updateUserOrder);
  });
});
