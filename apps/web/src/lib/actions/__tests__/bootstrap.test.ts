import { describe, it, expect, vi, beforeEach } from "vitest";

// Server actions transitively import `server-only`, which throws in a
// non-Next runtime. Neutralize it before any module under test loads.
vi.mock("server-only", () => ({}));

// db.execute is the only side-effect path in the bootstrap action.
const mockExecute = vi.fn();
vi.mock("@/db", () => ({
  db: { execute: (...args: unknown[]) => mockExecute(...args) },
}));

// Session resolution is exercised separately. Stub it so we control the
// branch under test.
const mockGetSession = vi.fn();
vi.mock("@/lib/sessionCache", () => ({
  getSession: () => mockGetSession(),
  getSessionUserId: () => mockGetSession()?.user?.id ?? null,
}));

// Drizzle's tagged-template `sql` helper — bootstrap.ts only constructs a
// query and passes it to db.execute, so a no-op stand-in is sufficient.
vi.mock("drizzle-orm", () => ({
  sql: (..._args: unknown[]) => ({ _isSql: true }),
}));

import { fetchAppBootstrap } from "../bootstrap";

beforeEach(() => {
  mockExecute.mockReset();
  mockGetSession.mockReset();
});

describe("fetchAppBootstrap", () => {
  it("returns the anonymous shape when no session", async () => {
    mockGetSession.mockResolvedValue(null);

    const result = await fetchAppBootstrap();

    expect(result).toEqual({
      user: null,
      prefs: null,
      savedStatuses: [],
      starredIds: [],
    });
    expect(mockExecute).not.toHaveBeenCalled();
  });

  it("maps the single-row JSON aggregate result to AppBootstrapData", async () => {
    mockGetSession.mockResolvedValue({
      user: { id: "u1", email: "a@b.c", name: "Alice", emailVerified: true },
    });

    mockExecute.mockResolvedValue([
      {
        prefs: {
          theme: "dark",
          themeUpdatedAt: "2026-04-25T10:00:00.000Z",
          locale: "de",
          localeUpdatedAt: "2026-04-25T10:00:00.000Z",
          cookieConsent: true,
          displayCurrency: "EUR",
          salaryPeriod: "year",
          dismissedBanners: ["upgrade"],
          jobLanguages: ["en", "de"],
        },
        saved_statuses: [
          { postingId: "p1", savedJobId: "s1", status: "saved" },
          { postingId: "p2", savedJobId: "s2", status: "applied" },
        ],
        starred_ids: [{ company_id: "c1" }, { company_id: "c2" }],
      },
    ]);

    const result = await fetchAppBootstrap();

    expect(result.user).toEqual({
      id: "u1",
      email: "a@b.c",
      name: "Alice",
      emailVerified: true,
    });
    expect(result.prefs?.theme).toBe("dark");
    expect(result.prefs?.locale).toBe("de");
    // Timestamps must come back as Date instances, not raw ISO strings.
    expect(result.prefs?.themeUpdatedAt).toBeInstanceOf(Date);
    expect(result.prefs?.localeUpdatedAt).toBeInstanceOf(Date);
    expect(result.savedStatuses).toEqual([
      { postingId: "p1", savedJobId: "s1", status: "saved" },
      { postingId: "p2", savedJobId: "s2", status: "applied" },
    ]);
    expect(result.starredIds).toEqual(["c1", "c2"]);
    expect(mockExecute).toHaveBeenCalledTimes(1);
  });

  it("handles a user with no prefs row + empty save/star sets", async () => {
    mockGetSession.mockResolvedValue({
      user: { id: "u2", email: "x@y.z", name: "New User", emailVerified: true },
    });

    mockExecute.mockResolvedValue([
      { prefs: null, saved_statuses: [], starred_ids: [] },
    ]);

    const result = await fetchAppBootstrap();

    expect(result.prefs).toBeNull();
    expect(result.savedStatuses).toEqual([]);
    expect(result.starredIds).toEqual([]);
  });

  it("survives a totally empty execute result (defensive)", async () => {
    // If the query somehow returns zero rows, we should not blow up — fall
    // back to anonymous-shaped data instead.
    mockGetSession.mockResolvedValue({
      user: { id: "u3", email: "x@y.z", name: "Edge", emailVerified: true },
    });
    mockExecute.mockResolvedValue([]);

    const result = await fetchAppBootstrap();

    expect(result.prefs).toBeNull();
    expect(result.savedStatuses).toEqual([]);
    expect(result.starredIds).toEqual([]);
  });

  it("preserves null timestamp fields without coercing to invalid Dates", async () => {
    mockGetSession.mockResolvedValue({
      user: { id: "u4", email: "x@y.z", name: "T", emailVerified: true },
    });
    mockExecute.mockResolvedValue([
      {
        prefs: {
          theme: "light",
          themeUpdatedAt: null,
          locale: "en",
          localeUpdatedAt: null,
          cookieConsent: false,
          displayCurrency: "EUR",
          salaryPeriod: null,
          dismissedBanners: [],
          jobLanguages: [],
        },
        saved_statuses: [],
        starred_ids: [],
      },
    ]);

    const result = await fetchAppBootstrap();

    expect(result.prefs?.themeUpdatedAt).toBeNull();
    expect(result.prefs?.localeUpdatedAt).toBeNull();
  });

  it("issues exactly one db.execute call (no parallel fan-out)", async () => {
    mockGetSession.mockResolvedValue({
      user: { id: "u5", email: "x@y.z", name: "Fan", emailVerified: true },
    });
    mockExecute.mockResolvedValue([
      { prefs: null, saved_statuses: [], starred_ids: [] },
    ]);

    await fetchAppBootstrap();

    expect(mockExecute).toHaveBeenCalledTimes(1);
  });
});
