import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getCurrencyRates: vi.fn(),
  getPublicWatchlistPostings: vi.fn(),
  getWatchlistPostings: vi.fn(),
  getWatchlistPostingYearCount: vi.fn(),
  logExternalError: vi.fn(),
  resolveLocationSlugs: vi.fn(),
  resolveOccupationSlugs: vi.fn(),
  resolveSenioritySlugs: vi.fn(),
  resolveTechnologySlugs: vi.fn(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getPublicWatchlistPostings: mocks.getPublicWatchlistPostings,
  getWatchlistPostings: mocks.getWatchlistPostings,
  getWatchlistPostingYearCount: mocks.getWatchlistPostingYearCount,
}));
vi.mock("@/lib/services/search", () => ({
  getCurrencyRates: mocks.getCurrencyRates,
}));
vi.mock("@/lib/services/locations", () => ({
  resolveLocationSlugs: mocks.resolveLocationSlugs,
}));
vi.mock("@/lib/services/taxonomy", () => ({
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));
vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

import {
  buildWatchlistPageData,
  WATCHLIST_SEARCH_BUDGET_MS,
} from "../watchlist-page-data";
import { malformedTypesenseResponseError } from "@/lib/search/typesense-retry";
import type { WatchlistDetail } from "@/lib/services/watchlists";

const detail: WatchlistDetail = {
  id: "watchlist-1",
  slug: "backend-jobs",
  title: "Backend jobs",
  description: null,
  isPublic: true,
  alertsEnabled: false,
  filters: { locationSlugs: ["zurich"] },
  sourceWatchlistId: null,
  createdAt: "2026-08-20T00:00:00.000Z",
  owner: {
    id: "owner-1",
    username: "alice",
    displayUsername: null,
    name: "Alice",
  },
  companies: [{
    id: "company-1",
    name: "Acme",
    slug: "acme",
    icon: null,
  }],
};

const params = {
  detail,
  locale: "en",
  isOwner: false,
  isPaidPlan: false,
  limitReached: true,
  jobLanguages: [] as string[],
  publicSnapshot: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getCurrencyRates.mockResolvedValue([]);
  mocks.getPublicWatchlistPostings.mockResolvedValue({ postings: [], total: 0 });
  mocks.getWatchlistPostings.mockResolvedValue({ postings: [], total: 0 });
  mocks.getWatchlistPostingYearCount.mockResolvedValue(0);
  mocks.resolveLocationSlugs.mockResolvedValue(new Map());
  mocks.resolveOccupationSlugs.mockResolvedValue(new Map());
  mocks.resolveSenioritySlugs.mockResolvedValue(new Map());
  mocks.resolveTechnologySlugs.mockResolvedValue(new Map());
});

afterEach(() => {
  vi.useRealTimers();
});

describe("watchlist page Typesense degradation (#7487)", () => {
  it.each([
    Object.assign(new Error("request timed out"), { code: "ETIMEDOUT" }),
    Object.assign(new Error("Not Ready or Lagging"), { httpStatus: 503 }),
    new Error("Not Ready or Lagging"),
    malformedTypesenseResponseError(),
  ])("keeps an authoritative existing watchlist usable for %s", async (error) => {
    mocks.resolveLocationSlugs.mockRejectedValueOnce(error);

    const result = await buildWatchlistPageData(params);

    expect(result.detail).toBe(detail);
    expect(result).toMatchObject({
      postings: [],
      total: 0,
      yearTotal: 0,
      browserPostingFilters: null,
    });
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      { service: "typesense", operation: "watchlist_page_data" },
      error,
    );
    expect(JSON.stringify(result)).not.toContain("Not Ready or Lagging");
  });

  it("keeps cached taxonomy resolution while enforcing the search budget", async () => {
    vi.useFakeTimers();
    mocks.resolveLocationSlugs.mockImplementationOnce(
      () => new Promise(() => {}),
    );

    const resultPromise = buildWatchlistPageData(params);
    await vi.advanceTimersByTimeAsync(WATCHLIST_SEARCH_BUDGET_MS);

    await expect(resultPromise).resolves.toMatchObject({
      detail,
      postings: [],
      total: 0,
      yearTotal: 0,
    });
    expect(mocks.resolveLocationSlugs).toHaveBeenCalledWith(["zurich"], "en");
    expect(mocks.getPublicWatchlistPostings).not.toHaveBeenCalled();
  });
});

describe("watchlist browser refresh input (#8258)", () => {
  it("serializes the exact resolved server query without its abort signal", async () => {
    mocks.resolveLocationSlugs.mockResolvedValueOnce(new Map([
      ["zurich", {
        id: 4,
        slug: "zurich",
        name: "Zurich",
        type: "city",
        parentName: "Switzerland",
      }],
    ]));

    const result = await buildWatchlistPageData(params);

    expect(result.browserPostingFilters).toEqual({
      companyIds: ["company-1"],
      anyCompany: undefined,
      keywords: undefined,
      locationIds: [4],
      occupationIds: [],
      seniorityIds: [],
      technologyIds: [],
      workMode: undefined,
      employmentType: undefined,
      salaryMin: undefined,
      salaryMax: undefined,
      experienceMin: undefined,
      experienceMax: undefined,
      languages: ["en"],
    });
    expect(result.browserPostingFilters).not.toHaveProperty("abortSignal");
  });
});
