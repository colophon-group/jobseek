import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  buildWatchlistPageData: vi.fn(),
  canCreateWatchlist: vi.fn(),
  getPreferences: vi.fn(),
  getSession: vi.fn(),
  getUserPlan: vi.fn(),
  getPublicWatchlistByUserAndSlug: vi.fn(),
  getWatchlistByUserAndSlug: vi.fn(),
  readAnonJobLanguagesCookie: vi.fn(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getPublicWatchlistByUserAndSlug: mocks.getPublicWatchlistByUserAndSlug,
  getWatchlistByUserAndSlug: mocks.getWatchlistByUserAndSlug,
}));
vi.mock("@/lib/services/watchlist-page-data", () => ({
  buildWatchlistPageData: mocks.buildWatchlistPageData,
}));
vi.mock("@/lib/sessionCache", () => ({ getSession: mocks.getSession }));
vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: mocks.canCreateWatchlist,
  getUserPlan: mocks.getUserPlan,
  PLAN_LIMITS: { free: { canReceiveAlerts: false } },
}));
vi.mock("@/lib/actions/preferences", () => ({
  getPreferences: mocks.getPreferences,
}));
vi.mock("@/lib/anon-preferences", () => ({
  readAnonJobLanguagesCookie: mocks.readAnonJobLanguagesCookie,
}));

import {
  fetchPublicWatchlistPageData,
  fetchWatchlistPageData,
} from "../watchlist-page-data";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getSession.mockResolvedValue(null);
  mocks.readAnonJobLanguagesCookie.mockResolvedValue([]);
});

describe("fetchWatchlistPageData authoritative boundary (#7487)", () => {
  it("rejects the former caller-supplied detail payload without a search", async () => {
    await expect(fetchPublicWatchlistPageData({
      detail: { id: "stale-client-detail" },
      locale: "en",
    })).resolves.toBeNull();

    expect(mocks.getPublicWatchlistByUserAndSlug).not.toHaveBeenCalled();
    expect(mocks.buildWatchlistPageData).not.toHaveBeenCalled();
  });

  it.each([
    ["timeout", Object.assign(new Error("request timed out"), { code: "ETIMEDOUT" })],
    ["503", Object.assign(new Error("Not Ready or Lagging"), { httpStatus: 503 })],
    ["not-ready", new Error("Not Ready or Lagging")],
    ["malformed response", Object.assign(new Error("Typesense response was malformed"), {
      typesenseUnavailable: true,
    })],
  ])("returns a missing slug before a %s search failure can run", async (_condition, error) => {
    mocks.getWatchlistByUserAndSlug.mockResolvedValue(null);
    mocks.buildWatchlistPageData.mockRejectedValue(error);

    await expect(fetchWatchlistPageData({
      userSlug: "alice",
      watchlistSlug: "missing",
      locale: "en",
    })).resolves.toBeNull();

    expect(mocks.buildWatchlistPageData).not.toHaveBeenCalled();
    expect(mocks.getSession).not.toHaveBeenCalled();
  });

  it("builds data only after Postgres confirms the route", async () => {
    const detail = { owner: { id: "owner-1" } };
    const built = { detail, postings: [], total: 0 };
    mocks.getWatchlistByUserAndSlug.mockResolvedValue(detail);
    mocks.buildWatchlistPageData.mockResolvedValue(built);

    await expect(fetchWatchlistPageData({
      userSlug: "alice",
      watchlistSlug: "backend-jobs",
      locale: "en",
    })).resolves.toBe(built);

    expect(mocks.buildWatchlistPageData).toHaveBeenCalledWith(
      expect.objectContaining({ detail, publicSnapshot: false }),
    );
  });
});
