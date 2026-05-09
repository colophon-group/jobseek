import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Server actions transitively import `server-only`, which throws in a
// non-Next runtime. Neutralize before module-under-test loads.
vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  revalidatePath: vi.fn(),
  cacheLife: vi.fn(),
  getSessionUserId: vi.fn(),
  getSession: vi.fn().mockResolvedValue(null),
  writeAnonJobLanguagesCookie: vi.fn(),
  readAnonJobLanguagesCookie: vi.fn(),
  // Drizzle surface — `update().set().where().returning()` and
  // `insert().values().onConflictDoUpdate().returning()` need to chain
  // and resolve. Each test wires up via `dbUpdateChain` /
  // `dbInsertChain` returning a thenable that yields a single row.
  selectLimitResult: vi.fn(),
  updateReturningResult: vi.fn(),
  insertReturningResult: vi.fn(),
}));

vi.mock("next/cache", () => ({
  revalidatePath: mocks.revalidatePath,
  cacheLife: mocks.cacheLife,
}));

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
  getSession: mocks.getSession,
}));

vi.mock("@/lib/anon-preferences", () => ({
  writeAnonJobLanguagesCookie: mocks.writeAnonJobLanguagesCookie,
  readAnonJobLanguagesCookie: mocks.readAnonJobLanguagesCookie,
}));

// Drizzle: minimal fluent-API stub that records calls and lets tests
// queue the leaf result.
const buildSelectChain = () => ({
  from: () => ({
    where: () => ({
      limit: () => mocks.selectLimitResult(),
    }),
  }),
});
const buildUpdateChain = () => ({
  set: () => ({
    where: () => ({
      returning: () => mocks.updateReturningResult(),
    }),
  }),
});
const buildInsertChain = () => ({
  values: () => ({
    onConflictDoUpdate: () => ({
      returning: () => mocks.insertReturningResult(),
    }),
  }),
});

vi.mock("@/db", () => ({
  db: {
    select: () => buildSelectChain(),
    update: () => buildUpdateChain(),
    insert: () => buildInsertChain(),
    execute: vi.fn(),
  },
}));

// Mock auth surface (only `auth.api.setPassword` is referenced via
// import-time evaluation; not exercised by these tests but the module
// graph still loads it).
vi.mock("@/lib/auth", () => ({ auth: { api: { setPassword: vi.fn() } } }));

const PATHS = [
  "/[lang]/(app)/explore",
  "/[lang]/(app)/[userSlug]/[watchlistSlug]",
  "/[lang]/(app)/company/[slug]",
];

describe("updatePreferences invalidates job-language-dependent pages (#2916)", () => {
  beforeEach(() => {
    mocks.revalidatePath.mockReset();
    mocks.getSessionUserId.mockReset();
    mocks.writeAnonJobLanguagesCookie.mockReset();
    mocks.selectLimitResult.mockReset();
    mocks.updateReturningResult.mockReset();
    mocks.insertReturningResult.mockReset();
  });

  afterEach(() => {
    vi.resetModules();
  });

  it("anon path: writes cookie and revalidates every dependent page", async () => {
    mocks.getSessionUserId.mockResolvedValue(null);
    const { updatePreferences } = await import("../preferences");

    await updatePreferences({ jobLanguages: ["fr"] });

    expect(mocks.writeAnonJobLanguagesCookie).toHaveBeenCalledWith(["fr"]);
    expect(mocks.revalidatePath).toHaveBeenCalledTimes(PATHS.length);
    for (const p of PATHS) {
      expect(mocks.revalidatePath).toHaveBeenCalledWith(p);
    }
  });

  it("anon path: skips revalidation when jobLanguages is not in payload", async () => {
    mocks.getSessionUserId.mockResolvedValue(null);
    const { updatePreferences } = await import("../preferences");

    await updatePreferences({ theme: "dark" });

    expect(mocks.writeAnonJobLanguagesCookie).not.toHaveBeenCalled();
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it("auth update path: revalidates when jobLanguages is set", async () => {
    mocks.getSessionUserId.mockResolvedValue("user-1");
    mocks.selectLimitResult.mockResolvedValue([
      { userId: "user-1", jobLanguages: [], dismissedBanners: [] },
    ]);
    mocks.updateReturningResult.mockResolvedValue([{ userId: "user-1", jobLanguages: ["fr"] }]);
    const { updatePreferences } = await import("../preferences");

    await updatePreferences({ jobLanguages: ["fr"] });

    expect(mocks.revalidatePath).toHaveBeenCalledTimes(PATHS.length);
    for (const p of PATHS) {
      expect(mocks.revalidatePath).toHaveBeenCalledWith(p);
    }
  });

  it("auth update path: does NOT revalidate when only theme changes", async () => {
    mocks.getSessionUserId.mockResolvedValue("user-1");
    mocks.selectLimitResult.mockResolvedValue([
      { userId: "user-1", jobLanguages: [], dismissedBanners: [] },
    ]);
    mocks.updateReturningResult.mockResolvedValue([{ userId: "user-1", theme: "dark" }]);
    const { updatePreferences } = await import("../preferences");

    await updatePreferences({ theme: "dark" });

    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it("auth insert path: revalidates when jobLanguages is set", async () => {
    mocks.getSessionUserId.mockResolvedValue("user-2");
    // No existing row → falls through to insert
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ userId: "user-2", jobLanguages: ["fr"] }]);
    const { updatePreferences } = await import("../preferences");

    await updatePreferences({ jobLanguages: ["fr"] });

    expect(mocks.revalidatePath).toHaveBeenCalledTimes(PATHS.length);
    for (const p of PATHS) {
      expect(mocks.revalidatePath).toHaveBeenCalledWith(p);
    }
  });

  it("revalidatePath failure is swallowed (preference write must not 500)", async () => {
    mocks.getSessionUserId.mockResolvedValue(null);
    mocks.revalidatePath.mockImplementation(() => {
      throw new Error("revalidate broken");
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { updatePreferences } = await import("../preferences");

    // Should resolve without throwing even though every revalidate call fails.
    await expect(updatePreferences({ jobLanguages: ["fr"] })).resolves.toBeNull();
    expect(mocks.writeAnonJobLanguagesCookie).toHaveBeenCalledWith(["fr"]);
    // One warn per path
    expect(warn).toHaveBeenCalledTimes(PATHS.length);
    warn.mockRestore();
  });
});
