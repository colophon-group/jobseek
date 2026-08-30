import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  browserListTopCompanies: vi.fn(),
  browserSearchJobs: vi.fn(),
  browserLoadCompanyPostings: vi.fn(),
  browserLoadSimilarCompanies: vi.fn(),
  browserWatchlistPostings: vi.fn(),
  browserWatchlistYearCount: vi.fn(),
  serverListTopCompanies: vi.fn(),
  serverSearchJobs: vi.fn(),
  serverGetCompanyPostings: vi.fn(),
  serverGetWatchlistPostings: vi.fn(),
  serverGetWatchlistYearCount: vi.fn(),
}));

vi.mock("@/lib/actions/search", () => ({
  listTopCompanies: mocks.serverListTopCompanies,
  searchJobs: mocks.serverSearchJobs,
}));

vi.mock("@/lib/actions/company", () => ({
  getCompanyPostings: mocks.serverGetCompanyPostings,
}));

vi.mock("@/lib/actions/watchlists", () => ({
  getWatchlistPostings: mocks.serverGetWatchlistPostings,
  getWatchlistPostingYearCount: mocks.serverGetWatchlistYearCount,
}));

vi.mock("../typesense-browser-watchlist", () => ({
  getWatchlistPostingsBrowser: mocks.browserWatchlistPostings,
  getWatchlistPostingYearCountBrowser: mocks.browserWatchlistYearCount,
}));

vi.mock("../typesense-browser", () => ({
  getBrowserSearchProvider: () => ({
    listTopCompanies: mocks.browserListTopCompanies,
    search: mocks.browserSearchJobs,
    loadPostingsWithCounts: mocks.browserLoadCompanyPostings,
    loadSimilarCompanies: mocks.browserLoadSimilarCompanies,
  }),
}));

describe("browser-direct shell refreshes", () => {
  withTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });
  });

  it("refreshes explore data without invoking the server fallback", async () => {
    const browserResult = {
      companies: [],
      totalCompanies: 42,
      truncated: false,
    };
    mocks.browserListTopCompanies.mockResolvedValue(browserResult);
    const { tryListTopCompaniesDirect } = await import("../search-runner");

    await expect(
      tryListTopCompaniesDirect(
        { languages: ["en"], locale: "en", offset: 0, limit: 10 },
        false,
      ),
    ).resolves.toEqual(browserResult);
    expect(mocks.serverListTopCompanies).not.toHaveBeenCalled();
  });

  it("searches a filtered Explore shell without invoking the server fallback", async () => {
    const browserResult = {
      companies: [],
      totalCompanies: 7,
      truncated: false,
    };
    mocks.browserSearchJobs.mockResolvedValue(browserResult);
    const { trySearchJobsDirect } = await import("../search-runner");

    await expect(
      trySearchJobsDirect(
        {
          keywords: ["python"],
          languages: ["en"],
          locale: "en",
          offset: 0,
          limit: 10,
        },
        false,
      ),
    ).resolves.toEqual(browserResult);
    expect(mocks.serverSearchJobs).not.toHaveBeenCalled();
  });

  it("returns null for a degraded direct Explore search", async () => {
    mocks.browserSearchJobs.mockResolvedValue({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });
    const { trySearchJobsDirect } = await import("../search-runner");

    await expect(
      trySearchJobsDirect(
        {
          keywords: ["python"],
          languages: ["en"],
          locale: "en",
          offset: 0,
          limit: 10,
        },
        false,
      ),
    ).resolves.toBeNull();
    expect(mocks.serverSearchJobs).not.toHaveBeenCalled();
  });

  it("returns null on a degraded browser result instead of consuming Fluid CPU", async () => {
    mocks.browserListTopCompanies.mockResolvedValue({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });
    const { tryListTopCompaniesDirect } = await import("../search-runner");

    await expect(
      tryListTopCompaniesDirect(
        { languages: ["en"], locale: "en", offset: 0, limit: 10 },
        false,
      ),
    ).resolves.toBeNull();
    expect(mocks.serverListTopCompanies).not.toHaveBeenCalled();
  });

  it("refreshes company postings without invoking the server fallback", async () => {
    const browserResult = {
      postings: [],
      activeCount: 7,
      yearCount: 11,
    };
    mocks.browserLoadCompanyPostings.mockResolvedValue(browserResult);
    const { tryGetCompanyPostingsDirect } = await import("../search-runner");

    await expect(
      tryGetCompanyPostingsDirect(
        {
          companyId: "company-1",
          keywords: [],
          languages: ["en"],
          locale: "en",
          offset: 0,
          limit: 20,
        },
        false,
      ),
    ).resolves.toEqual(browserResult);
    expect(mocks.serverGetCompanyPostings).not.toHaveBeenCalled();
  });

  it("refreshes a public watchlist snapshot without invoking server fallbacks", async () => {
    mocks.browserWatchlistPostings.mockResolvedValue({
      postings: [],
      total: 7,
    });
    mocks.browserWatchlistYearCount.mockResolvedValue(19);
    const { tryGetWatchlistSnapshotDirect } = await import("../search-runner");

    await expect(
      tryGetWatchlistSnapshotDirect({
        companyIds: ["company-1"],
        locationIds: [4],
        languages: ["en"],
      }),
    ).resolves.toEqual({ postings: [], total: 7, yearTotal: 19 });
    expect(mocks.browserWatchlistPostings).toHaveBeenCalledWith({
      companyIds: ["company-1"],
      locationIds: [4],
      languages: ["en"],
      offset: 0,
      limit: 20,
    });
    expect(mocks.serverGetWatchlistPostings).not.toHaveBeenCalled();
    expect(mocks.serverGetWatchlistYearCount).not.toHaveBeenCalled();
  });

  it("keeps the watchlist shell when browser validation rejects a malformed success", async () => {
    mocks.browserWatchlistPostings.mockRejectedValue(
      new Error("Typesense response was malformed"),
    );
    mocks.browserWatchlistYearCount.mockResolvedValue(19);
    const { tryGetWatchlistSnapshotDirect } = await import("../search-runner");

    await expect(
      tryGetWatchlistSnapshotDirect({ companyIds: ["company-1"] }),
    ).resolves.toBeNull();
    expect(mocks.serverGetWatchlistPostings).not.toHaveBeenCalled();
    expect(mocks.serverGetWatchlistYearCount).not.toHaveBeenCalled();
  });

  it("does nothing when browser-direct search is disabled", async () => {
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "0" });
    vi.resetModules();
    const {
      tryGetCompanyPostingsDirect,
      tryGetSimilarCompaniesDirect,
      tryGetWatchlistSnapshotDirect,
      tryListTopCompaniesDirect,
    } = await import("../search-runner");

    await expect(
      tryListTopCompaniesDirect(
        { languages: ["en"], locale: "en", offset: 0, limit: 10 },
        false,
      ),
    ).resolves.toBeNull();
    await expect(
      tryGetCompanyPostingsDirect(
        {
          companyId: "company-1",
          keywords: [],
          languages: ["en"],
          locale: "en",
          offset: 0,
          limit: 20,
        },
        false,
      ),
    ).resolves.toBeNull();
    await expect(
      tryGetSimilarCompaniesDirect({
        companyId: "company-1",
        industryId: 7,
        limit: 10,
      }),
    ).resolves.toBeNull();
    await expect(
      tryGetWatchlistSnapshotDirect({ companyIds: ["company-1"] }),
    ).resolves.toBeNull();
    expect(mocks.browserListTopCompanies).not.toHaveBeenCalled();
    expect(mocks.browserSearchJobs).not.toHaveBeenCalled();
    expect(mocks.browserLoadCompanyPostings).not.toHaveBeenCalled();
    expect(mocks.browserLoadSimilarCompanies).not.toHaveBeenCalled();
    expect(mocks.browserWatchlistPostings).not.toHaveBeenCalled();
    expect(mocks.browserWatchlistYearCount).not.toHaveBeenCalled();
  });

  it("refreshes similar companies without invoking a Server Action", async () => {
    const browserResult = {
      companies: [],
      hasMore: false,
    };
    mocks.browserLoadSimilarCompanies.mockResolvedValue(browserResult);
    const { tryGetSimilarCompaniesDirect } = await import("../search-runner");

    await expect(
      tryGetSimilarCompaniesDirect({
        companyId: "company-1",
        industryId: 7,
        limit: 10,
      }),
    ).resolves.toEqual(browserResult);
    expect(mocks.browserLoadSimilarCompanies).toHaveBeenCalledWith(
      "company-1",
      7,
      10,
    );
    expect(mocks.serverGetCompanyPostings).not.toHaveBeenCalled();
  });
});
