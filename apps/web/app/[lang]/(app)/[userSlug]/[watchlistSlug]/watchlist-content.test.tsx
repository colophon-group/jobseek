import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WatchlistPageData } from "@/lib/actions/watchlist-page-data";

const mockFetchWatchlistPageData = vi.fn();
const mockHasLoggedInHint = vi.fn();
const mockHasAnonJobLanguagesHint = vi.fn();

vi.mock("@/lib/actions/watchlist-page-data", () => ({
  fetchWatchlistPageData: (...args: unknown[]) =>
    mockFetchWatchlistPageData(...args),
}));

vi.mock("@/lib/client-cookies", () => ({
  hasLoggedInHint: () => mockHasLoggedInHint(),
  hasAnonJobLanguagesHint: () => mockHasAnonJobLanguagesHint(),
}));

vi.mock("@/components/search/watchlist-skeleton", () => ({
  WatchlistSkeleton: () => <div data-testid="watchlist-skeleton" />,
}));

vi.mock("./watchlist-view-page", () => ({
  WatchlistViewPage: ({ isOwner }: { isOwner: boolean }) => (
    <div data-testid="watchlist-view" data-owner={String(isOwner)} />
  ),
}));

vi.mock("./watchlist-not-found", () => ({
  WatchlistNotFoundState: () => <div data-testid="watchlist-not-found" />,
}));

import { WatchlistContent } from "./watchlist-content";

function makeData(isOwner = false): WatchlistPageData {
  return {
    detail: {
      id: "watchlist-1",
      slug: "public-list",
      title: "Public list",
      description: null,
      isPublic: true,
      alertsEnabled: false,
      filters: {},
      sourceWatchlistId: null,
      createdAt: "2026-07-22T00:00:00.000Z",
      owner: {
        id: "user-1",
        username: "owner",
        displayUsername: "owner",
        name: "Owner",
      },
      companies: [],
    },
    isOwner,
    isPaidPlan: false,
    limitReached: true,
    postings: [],
    total: 0,
    yearTotal: 0,
    resolvedLocations: [],
    resolvedOccupations: [],
    resolvedSeniorities: [],
    resolvedTechnologies: [],
    jobLanguages: [],
    languages: ["en"],
  };
}

beforeEach(() => {
  mockFetchWatchlistPageData.mockReset();
  mockHasLoggedInHint.mockReset().mockReturnValue(false);
  mockHasAnonJobLanguagesHint.mockReset().mockReturnValue(false);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WatchlistContent initial data", () => {
  it("renders anonymous public data immediately without a mount fetch", async () => {
    const { getByTestId, queryByTestId } = render(
      <WatchlistContent
        lang="en"
        userSlug="owner"
        watchlistSlug="public-list"
        initialData={makeData()}
      />,
    );

    expect(getByTestId("watchlist-view")).toBeTruthy();
    expect(queryByTestId("watchlist-skeleton")).toBeNull();
    await waitFor(() => expect(mockFetchWatchlistPageData).not.toHaveBeenCalled());
  });

  it("refetches when the logged-in hint requires viewer-specific data", async () => {
    mockHasLoggedInHint.mockReturnValue(true);
    mockFetchWatchlistPageData.mockResolvedValue(makeData(true));

    const { getByTestId } = render(
      <WatchlistContent
        lang="en"
        userSlug="owner"
        watchlistSlug="public-list"
        initialData={makeData()}
      />,
    );

    await waitFor(() => {
      expect(mockFetchWatchlistPageData).toHaveBeenCalledWith({
        userSlug: "owner",
        watchlistSlug: "public-list",
        locale: "en",
      });
      expect(getByTestId("watchlist-view").getAttribute("data-owner")).toBe(
        "true",
      );
    });
  });

  it("uses viewer-resolved server data without another authenticated fetch", async () => {
    mockHasLoggedInHint.mockReturnValue(true);

    const { getByTestId } = render(
      <WatchlistContent
        lang="en"
        userSlug="owner"
        watchlistSlug="private-list"
        initialData={makeData(true)}
        viewerResolved
      />,
    );

    expect(getByTestId("watchlist-view").getAttribute("data-owner")).toBe(
      "true",
    );
    await waitFor(() => expect(mockFetchWatchlistPageData).not.toHaveBeenCalled());
  });

  it("renders a definitive server not-found result without a client lookup", async () => {
    const { getByTestId, queryByTestId } = render(
      <WatchlistContent
        lang="en"
        userSlug="missing"
        watchlistSlug="missing"
        initialData={null}
        viewerResolved
      />,
    );

    expect(getByTestId("watchlist-not-found")).toBeTruthy();
    expect(queryByTestId("watchlist-skeleton")).toBeNull();
    await waitFor(() => expect(mockFetchWatchlistPageData).not.toHaveBeenCalled());
  });

  it("keeps the legacy fetch path when no public initial data exists", async () => {
    mockFetchWatchlistPageData.mockResolvedValue(null);

    render(
      <WatchlistContent
        lang="en"
        userSlug="missing"
        watchlistSlug="missing"
      />,
    );

    await waitFor(() => expect(mockFetchWatchlistPageData).toHaveBeenCalledTimes(1));
  });
});
