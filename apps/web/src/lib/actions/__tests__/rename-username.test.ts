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

    expect(result).toEqual({ error: "not_authenticated" });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("validates length", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    const { renameUsername } = await import("../preferences");

    expect(await renameUsername("ab")).toEqual({
      error: "username_length",
    });
    expect(
      await renameUsername("a".repeat(31)),
    ).toEqual({ error: "username_length" });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("validates character set after lowercase-normalization", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    const { renameUsername } = await import("../preferences");

    // Hyphen at the start / end is rejected by the regex (matches the
    // client-side check in `UsernameSection`).
    expect(await renameUsername("-leading-hyphen")).toEqual({
      error: "username_invalid_characters",
    });
    expect(await renameUsername("trailing-")).toEqual({
      error: "username_invalid_characters",
    });
    // Special chars that survive normalization but aren't [a-z0-9-].
    expect(await renameUsername("has space")).toEqual({
      error: "username_invalid_characters",
    });
    expect(await renameUsername("with.dot")).toEqual({
      error: "username_invalid_characters",
    });
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("rejects reserved usernames", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u1" } });
    const { renameUsername } = await import("../preferences");

    expect(await renameUsername("admin")).toEqual({
      error: "username_reserved",
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

  it("does not republish retired watchlist discovery during a rename", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.selectQueue.push([
      { username: "old", displayUsername: "old" },
    ]);
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("new");
    expect(result).toEqual({});

    // Better Auth was asked to do the actual rename.
    expect(mocks.updateUser).toHaveBeenCalledTimes(1);
    expect(mocks.updateUser).toHaveBeenCalledWith(
      expect.objectContaining({ body: { username: "new" } }),
    );

    expect(mocks.invalidateAllUserSessionCacheEntries).toHaveBeenCalledWith(
      "user-1",
    );
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateRedis).not.toHaveBeenCalled();
    expect(mocks.tsUpdateWatchlistField).not.toHaveBeenCalled();
    expect(mocks.notifyIndexNow).not.toHaveBeenCalled();
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

  it("returns an opaque Better Auth error code and does NOT run the fanout", async () => {
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
    expect(result).toEqual({ error: "username_update_failed" });
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateRedis).not.toHaveBeenCalled();
    expect(mocks.tsUpdateWatchlistField).not.toHaveBeenCalled();
    expect(mocks.invalidateAllUserSessionCacheEntries).not.toHaveBeenCalled();
    expect(mocks.notifyIndexNow).not.toHaveBeenCalled();
  });

  it("succeeds with no watchlists and still busts the session cache", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u5" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    mocks.executeQueue.push([]); // no watchlists
    const { renameUsername } = await import("../preferences");

    const result = await renameUsername("new");
    expect(result).toEqual({});
    expect(mocks.updateUser).toHaveBeenCalled();
    expect(mocks.tsUpdateWatchlistField).not.toHaveBeenCalled();
    // No per-watchlist cache tags exist, but the session cache bust still runs.
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateAllUserSessionCacheEntries).toHaveBeenCalledWith(
      "u5",
    );
  });

  it("invalidates sessions only after Better Auth commits the rename", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "u-order" } });
    mocks.selectQueue.push([{ username: "old", displayUsername: null }]);
    const { renameUsername } = await import("../preferences");

    await renameUsername("new");

    const updateUserOrder = mocks.updateUser.mock.invocationCallOrder[0];
    const sessionBustOrder =
      mocks.invalidateAllUserSessionCacheEntries.mock.invocationCallOrder[0];

    expect(updateUserOrder).toBeDefined();
    expect(sessionBustOrder).toBeGreaterThan(updateUserOrder);
    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidateRedis).not.toHaveBeenCalled();
    expect(mocks.tsUpdateWatchlistField).not.toHaveBeenCalled();
    expect(mocks.notifyIndexNow).not.toHaveBeenCalled();
  });
});
