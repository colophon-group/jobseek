import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WatchlistPageData } from "@/lib/actions/watchlist-page-data";

const mockFetchWatchlistPageData = vi.fn();
const mockHasLoggedInHint = vi.fn();
const mockReadAnonJobLanguagesPreference = vi.fn();
const mockTryGetWatchlistSnapshotDirect = vi.fn();

vi.mock("@/lib/actions/watchlist-page-data", () => ({
  fetchWatchlistPageData: (...args: unknown[]) =>
    mockFetchWatchlistPageData(...args),
}));

vi.mock("@/lib/client-cookies", () => ({
  hasLoggedInHint: () => mockHasLoggedInHint(),
  readAnonJobLanguagesPreference: () =>
    mockReadAnonJobLanguagesPreference(),
}));

vi.mock("@/lib/search/search-runner", () => ({
  tryGetWatchlistSnapshotDirect: (...args: unknown[]) =>
    mockTryGetWatchlistSnapshotDirect(...args),
}));

vi.mock("@/components/search/watchlist-skeleton", () => ({
  WatchlistSkeleton: () => <div data-testid="watchlist-skeleton" />,
}));

vi.mock("./watchlist-view-page", () => ({
  WatchlistViewPage: ({
    isOwner,
    initialTotal,
    yearTotal,
  }: {
    isOwner: boolean;
    initialTotal: number;
    yearTotal: number;
  }) => (
    <div
      data-testid="watchlist-view"
      data-owner={String(isOwner)}
      data-total={String(initialTotal)}
      data-year-total={String(yearTotal)}
    />
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
    browserPostingFilters: {
      companyIds: [],
      anyCompany: true,
      languages: ["en"],
    },
  };
}

beforeEach(() => {
  mockFetchWatchlistPageData.mockReset();
  mockHasLoggedInHint.mockReset().mockReturnValue(false);
  mockReadAnonJobLanguagesPreference.mockReset().mockReturnValue(null);
  mockTryGetWatchlistSnapshotDirect.mockReset().mockResolvedValue(null);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WatchlistContent initial data", () => {
  it("renders anonymous SSR data and refreshes directly without a server action", async () => {
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
    expect(mockTryGetWatchlistSnapshotDirect).toHaveBeenCalledWith({
      companyIds: [],
      anyCompany: true,
      languages: ["en"],
    });
  });

  it("replaces SSR postings and counts after a successful direct refresh", async () => {
    mockTryGetWatchlistSnapshotDirect.mockResolvedValue({
      postings: [],
      total: 12,
      yearTotal: 34,
    });

    const { getByTestId } = render(
      <WatchlistContent
        lang="en"
        userSlug="owner"
        watchlistSlug="public-list"
        initialData={makeData()}
      />,
    );

    await waitFor(() => {
      expect(getByTestId("watchlist-view").getAttribute("data-total")).toBe(
        "12",
      );
      expect(
        getByTestId("watchlist-view").getAttribute("data-year-total"),
      ).toBe("34");
    });
    expect(mockFetchWatchlistPageData).not.toHaveBeenCalled();
  });

  it("applies anonymous language preferences without a server action", async () => {
    mockReadAnonJobLanguagesPreference.mockReturnValue(["de"]);
    mockTryGetWatchlistSnapshotDirect.mockResolvedValue({
      postings: [],
      total: 3,
      yearTotal: 8,
    });

    render(
      <WatchlistContent
        lang="en"
        userSlug="owner"
        watchlistSlug="public-list"
        initialData={makeData()}
      />,
    );

    await waitFor(() => {
      expect(mockTryGetWatchlistSnapshotDirect).toHaveBeenCalledWith({
        companyIds: [],
        anyCompany: true,
        languages: ["de"],
      });
    });
    expect(mockFetchWatchlistPageData).not.toHaveBeenCalled();
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
    expect(mockTryGetWatchlistSnapshotDirect).not.toHaveBeenCalled();
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
    expect(mockTryGetWatchlistSnapshotDirect).not.toHaveBeenCalled();
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
    expect(mockTryGetWatchlistSnapshotDirect).not.toHaveBeenCalled();
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
    expect(mockTryGetWatchlistSnapshotDirect).not.toHaveBeenCalled();
  });
});
