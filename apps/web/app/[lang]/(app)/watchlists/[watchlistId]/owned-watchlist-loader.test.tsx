import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  getSession: vi.fn(),
  getOwned: vi.fn(),
  getShared: vi.fn(),
  build: vi.fn(),
  getViewerJobLanguages: vi.fn(),
  canCreate: vi.fn(),
  getAiFilterOwnerState: vi.fn(),
  listAiFilterDecisions: vi.fn(),
  viewProps: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  notFound: () => mocks.notFound(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { prefetch?: boolean }) => (
    <a href={href} data-prefetch={String(prefetch)} {...props}>{children}</a>
  ),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSession: () => mocks.getSession(),
}));

vi.mock("@/lib/watchlist-id", () => ({
  isWatchlistId: (value: string) =>
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getOwnedWatchlistById: (...args: unknown[]) => mocks.getOwned(...args),
  getSharedWatchlistById: (...args: unknown[]) => mocks.getShared(...args),
}));

vi.mock("@/lib/services/watchlist-page-data", () => ({
  buildWatchlistPageData: (...args: unknown[]) => mocks.build(...args),
}));

vi.mock("@/lib/actions/preferences", () => ({
  getViewerJobLanguages: () => mocks.getViewerJobLanguages(),
}));

vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: (...args: unknown[]) => mocks.canCreate(...args),
}));

vi.mock("@/lib/ai-filter/configuration-service", () => ({
  AiFilterNotFoundError: class AiFilterNotFoundError extends Error {},
  getAiFilterOwnerState: (...args: unknown[]) =>
    mocks.getAiFilterOwnerState(...args),
}));

vi.mock("@/lib/ai-filter/decision-service", () => ({
  listAiFilterDecisions: (...args: unknown[]) =>
    mocks.listAiFilterDecisions(...args),
}));

vi.mock("@/lib/ai-filter/candidate-loader", () => ({
  AiFilterCandidateLoadError: class AiFilterCandidateLoadError extends Error {},
}));

vi.mock("../../[userSlug]/[watchlistSlug]/watchlist-view-page", () => ({
  WatchlistViewPage: (props: {
    detail: Record<string, unknown> & { id: string };
    isOwner: boolean;
    limitReached: boolean;
    initialAiFilterState: unknown;
    initialAiAcceptedPage: unknown;
  }) => {
    mocks.viewProps(props);
    const {
    detail,
    isOwner,
    limitReached,
    } = props;
    return (
    <div
      data-testid="watchlist-detail"
      data-id={detail.id}
      data-is-owner={String(isOwner)}
      data-limit-reached={String(limitReached)}
      data-detail-keys={Object.keys(detail).sort().join(",")}
    />
    );
  },
}));

import { OwnedWatchlistLoader } from "./owned-watchlist-loader";
import { AiFilterNotFoundError } from "@/lib/ai-filter/configuration-service";
import { AiFilterCandidateLoadError } from "@/lib/ai-filter/candidate-loader";

const WATCHLIST_ID = "11111111-1111-4111-8111-111111111111";
const USER_ID = "user-1";

const detail = {
  id: WATCHLIST_ID,
  slug: "engineering",
  title: "Engineering",
  description: "Platform roles",
  alertsEnabled: true,
  filters: {},
  companies: [],
};

const ownerViewDetail = {
  id: WATCHLIST_ID,
  title: "Engineering",
  description: "Platform roles",
  alertsEnabled: true,
  filters: {},
  companies: [],
};

const sharedViewDetail = {
  id: WATCHLIST_ID,
  title: "Engineering",
  description: "Platform roles",
  filters: {},
  companies: [],
};

const pageData = {
  detail,
  postings: [],
  total: 0,
  yearTotal: 0,
  searchUnavailable: false,
  resolvedLocations: [],
  resolvedOccupations: [],
  resolvedSeniorities: [],
  resolvedTechnologies: [],
  jobLanguages: ["en"],
  languages: [],
};

describe("OwnedWatchlistLoader direct private detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSession.mockResolvedValue({ user: { id: USER_ID } });
    mocks.getOwned.mockResolvedValue(detail);
    mocks.getShared.mockResolvedValue(null);
    mocks.getViewerJobLanguages.mockResolvedValue(["en"]);
    mocks.canCreate.mockResolvedValue({ allowed: false });
    mocks.getAiFilterOwnerState.mockRejectedValue(new AiFilterNotFoundError());
    mocks.listAiFilterDecisions.mockResolvedValue({
      decisions: [],
      nextOffset: 0,
      hasMore: true,
    });
    mocks.build.mockImplementation(async (params: { detail: typeof detail }) => ({
      ...pageData,
      detail: params.detail,
      limitReached: (params as { limitReached?: boolean }).limitReached ?? false,
    }));
  });

  it("rejects a malformed id before reading session or watchlist data", async () => {
    await expect(OwnedWatchlistLoader({
      locale: "en",
      watchlistId: "not-a-watchlist-id",
      overviewLabel: "Watchlists",
    })).rejects.toThrow("NEXT_NOT_FOUND");

    expect(mocks.getSession).not.toHaveBeenCalled();
    expect(mocks.getOwned).not.toHaveBeenCalled();
    expect(mocks.getShared).not.toHaveBeenCalled();
  });

  it("returns the same not-found boundary for anonymous and non-owner requests", async () => {
    mocks.getSession.mockResolvedValueOnce(null);
    await expect(OwnedWatchlistLoader({
      locale: "en",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    })).rejects.toThrow("NEXT_NOT_FOUND");
    expect(mocks.getOwned).not.toHaveBeenCalled();
    expect(mocks.getShared).toHaveBeenCalledWith(WATCHLIST_ID);

    mocks.getSession.mockResolvedValue({ user: { id: "user-2" } });
    mocks.getOwned.mockResolvedValue(null);
    mocks.getShared.mockClear();
    await expect(OwnedWatchlistLoader({
      locale: "en",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    })).rejects.toThrow("NEXT_NOT_FOUND");
    expect(mocks.getOwned).toHaveBeenCalledWith(WATCHLIST_ID, "user-2");
    expect(mocks.getShared).toHaveBeenCalledWith(WATCHLIST_ID);
    expect(mocks.build).not.toHaveBeenCalled();
  });

  it("renders an explicitly shared id for an anonymous viewer without owner side effects", async () => {
    mocks.getSession.mockResolvedValue(null);
    mocks.getOwned.mockResolvedValue(null);
    mocks.getShared.mockResolvedValue({
      ...detail,
      isPublic: true,
      sourceWatchlistId: "private-source-id",
      createdAt: "2026-09-10T00:00:00.000Z",
      owner: {
        id: "private-owner-id",
        username: "private-owner",
        displayUsername: "private-owner",
        name: "Private Owner",
      },
    });

    render(await OwnedWatchlistLoader({
      locale: "en",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    }));

    expect(mocks.getOwned).not.toHaveBeenCalled();
    expect(mocks.getShared).toHaveBeenCalledWith(WATCHLIST_ID);
    expect(mocks.canCreate).not.toHaveBeenCalled();
    expect(mocks.build).toHaveBeenCalledWith({
      detail: sharedViewDetail,
      locale: "en",
      isOwner: false,
      limitReached: false,
      jobLanguages: ["en"],
      publicSnapshot: true,
    });
    expect(screen.getByTestId("watchlist-detail").getAttribute("data-is-owner"))
      .toBe("false");
    expect(screen.getByTestId("watchlist-detail").getAttribute("data-detail-keys"))
      .toBe("companies,description,filters,id,title");
  });

  it("checks the authenticated non-owner's clone limit", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-2" } });
    mocks.getOwned.mockResolvedValue(null);
    mocks.getShared.mockResolvedValue(detail);
    mocks.canCreate.mockResolvedValue({ allowed: false });

    render(await OwnedWatchlistLoader({
      locale: "en",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    }));

    expect(mocks.canCreate).toHaveBeenCalledWith("user-2");
    expect(mocks.build).toHaveBeenCalledWith(expect.objectContaining({
      isOwner: false,
      limitReached: true,
      publicSnapshot: true,
    }));
    expect(screen.getByTestId("watchlist-detail").getAttribute("data-limit-reached"))
      .toBe("true");
  });

  it("owner-validates by exact id before building and rendering detail data", async () => {
    render(await OwnedWatchlistLoader({
      locale: "de",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    }));

    expect(mocks.getOwned).toHaveBeenCalledWith(WATCHLIST_ID, USER_ID);
    expect(mocks.canCreate).toHaveBeenCalledWith(USER_ID);
    expect(mocks.build).toHaveBeenCalledWith({
      detail: ownerViewDetail,
      locale: "de",
      isOwner: true,
      limitReached: true,
      jobLanguages: ["en"],
      publicSnapshot: false,
    });

    const rendered = screen.getByTestId("watchlist-detail");
    expect(rendered.getAttribute("data-id")).toBe(WATCHLIST_ID);
    expect(rendered.getAttribute("data-is-owner")).toBe("true");
    const overviewLink = screen.getByRole("link", { name: "Watchlists" });
    expect(overviewLink.getAttribute("href")).toBe("/de/watchlists");
    expect(overviewLink.getAttribute("data-prefetch")).toBe("false");
  });

  it("restores the owner's saved matching request and accepted results", async () => {
    const aiState = {
      watchlistId: WATCHLIST_ID,
      enabled: true,
      entitled: true,
      query: "Backend roles without management",
      queryRevision: 2,
      queryVersionId: "22222222-2222-4222-8222-222222222222",
      status: "processing",
      counts: { accepted: 1, rejected: 2, total: 3 },
      progress: { selectionOffset: 0, scannedCount: 3, completedCount: 3, stopReason: null },
      lastCaughtUpAt: null,
      latestEventSequence: 4,
    };
    const posting = { id: "posting-1" };
    mocks.getAiFilterOwnerState.mockResolvedValue(aiState);
    mocks.listAiFilterDecisions.mockResolvedValue({
      decisions: [{ posting }],
      nextOffset: 3,
      hasMore: true,
    });

    render(await OwnedWatchlistLoader({
      locale: "en",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    }));

    expect(mocks.getAiFilterOwnerState).toHaveBeenCalledWith({
      ownerId: USER_ID,
      watchlistId: WATCHLIST_ID,
    });
    expect(mocks.listAiFilterDecisions).toHaveBeenCalledWith(expect.objectContaining({
      ownerId: USER_ID,
      watchlistId: WATCHLIST_ID,
      bucket: "accepted",
      offset: 0,
      limit: 20,
    }));
    expect(mocks.viewProps.mock.lastCall?.[0]).toEqual(expect.objectContaining({
      initialAiFilterState: aiState,
      initialAiAcceptedPage: {
        postings: [posting],
        nextOffset: 3,
        hasMore: true,
      },
    }));
  });

  it("keeps the owned route available when matching results cannot be read", async () => {
    const aiState = {
      watchlistId: WATCHLIST_ID,
      enabled: true,
      entitled: true,
      query: "Backend roles",
      queryRevision: 1,
      queryVersionId: "22222222-2222-4222-8222-222222222222",
      status: "provider_unavailable",
      counts: { accepted: 0, rejected: 0, total: 0 },
      progress: { selectionOffset: 0, scannedCount: 0, completedCount: 0, stopReason: "search_unavailable" },
      lastCaughtUpAt: null,
      latestEventSequence: 2,
    };
    mocks.getAiFilterOwnerState.mockResolvedValue(aiState);
    mocks.listAiFilterDecisions.mockRejectedValue(
      new AiFilterCandidateLoadError("search_unavailable"),
    );

    render(await OwnedWatchlistLoader({
      locale: "en",
      watchlistId: WATCHLIST_ID,
      overviewLabel: "Watchlists",
    }));

    expect(screen.getByTestId("watchlist-detail")).toBeTruthy();
    expect(mocks.viewProps.mock.lastCall?.[0]).toEqual(expect.objectContaining({
      initialAiFilterState: aiState,
      initialAiAcceptedPage: null,
    }));
  });
});
