import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  buildWatchlistPageData: vi.fn(),
  getPublicWatchlistByUserAndSlug: vi.fn(),
  getWatchlistByUserAndSlug: vi.fn(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getPublicWatchlistByUserAndSlug: mocks.getPublicWatchlistByUserAndSlug,
  getWatchlistByUserAndSlug: mocks.getWatchlistByUserAndSlug,
}));
vi.mock("@/lib/services/watchlist-page-data", () => ({
  buildWatchlistPageData: mocks.buildWatchlistPageData,
}));

import {
  fetchPublicWatchlistPageData,
  fetchWatchlistPageData,
} from "../watchlist-page-data";

describe("retired slug-page action IDs", () => {
  it.each([
    [
      "public snapshot",
      () =>
        fetchPublicWatchlistPageData({
          userSlug: "alice",
          watchlistSlug: "grandfathered-public-list",
          locale: "en",
        }),
    ],
    [
      "personalized page",
      () =>
        fetchWatchlistPageData({
          userSlug: "alice",
          watchlistSlug: "grandfathered-public-list",
          locale: "en",
        }),
    ],
  ])("fails closed for the former %s action", async (_label, invoke) => {
    mocks.getPublicWatchlistByUserAndSlug.mockResolvedValue({ id: "leak" });
    mocks.getWatchlistByUserAndSlug.mockResolvedValue({ id: "leak" });
    mocks.buildWatchlistPageData.mockResolvedValue({ detail: { id: "leak" } });

    await expect(invoke()).resolves.toBeNull();
    expect(mocks.getPublicWatchlistByUserAndSlug).not.toHaveBeenCalled();
    expect(mocks.getWatchlistByUserAndSlug).not.toHaveBeenCalled();
    expect(mocks.buildWatchlistPageData).not.toHaveBeenCalled();
  });
});
