import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  buildWatchlistPageData: vi.fn(),
  getSharedWatchlistById: vi.fn(),
  getWatchlistActivityPreviewsForDrafts: vi.fn(),
  getViewerJobLanguages: vi.fn(),
  readCompanyReferences: vi.fn(),
}));

vi.mock("@/lib/services/company-reference-read", () => ({
  readCompanyReferences: mocks.readCompanyReferences,
}));
vi.mock("@/lib/actions/preferences", () => ({
  getViewerJobLanguages: mocks.getViewerJobLanguages,
}));
vi.mock("@/lib/services/watchlist-page-data", () => ({
  buildWatchlistPageData: mocks.buildWatchlistPageData,
}));
vi.mock("@/lib/services/watchlists", () => ({
  getSharedWatchlistById: mocks.getSharedWatchlistById,
  getWatchlistActivityPreviewsForDrafts: mocks.getWatchlistActivityPreviewsForDrafts,
}));

import {
  getSessionWatchlistActivityPreviews,
  getSessionWatchlistPageData,
} from "../session-watchlists";

const sessionWatchlistId = "11111111-1111-4111-8111-111111111111";
const sourceWatchlistId = "22222222-2222-4222-8222-222222222222";
const pageData = { detail: { id: sessionWatchlistId }, postings: [], total: 0 };

describe("getSessionWatchlistPageData", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getViewerJobLanguages.mockResolvedValue(["en"]);
    mocks.readCompanyReferences.mockResolvedValue([]);
    mocks.buildWatchlistPageData.mockResolvedValue(pageData);
    mocks.getWatchlistActivityPreviewsForDrafts.mockResolvedValue({
      [sessionWatchlistId]: {
        activeJobCount: 24,
        activeCompanyCount: 3,
        topCompanies: [],
      },
    });
  });

  it("builds the normal watchlist page payload from an anonymous create draft", async () => {
    const draft = {
      title: "Swiss internships",
      companyIds: [],
      filters: { anyCompany: true, locationSlugs: ["switzerland"] },
      isPublic: false as const,
    };

    await expect(getSessionWatchlistPageData({
      sessionWatchlistId,
      locale: "en",
      intent: { kind: "create", draft },
    })).resolves.toEqual({ data: pageData, draft });

    expect(mocks.readCompanyReferences).toHaveBeenCalledWith([]);
    expect(mocks.buildWatchlistPageData).toHaveBeenCalledWith({
      detail: {
        id: sessionWatchlistId,
        title: draft.title,
        filters: draft.filters,
        companies: [],
        alertsEnabled: false,
      },
      locale: "en",
      isOwner: true,
      limitReached: false,
      jobLanguages: ["en"],
      publicSnapshot: false,
    });
  });

  it("preserves an unresolved selected company in the draft and matching scope", async () => {
    const id = "33333333-3333-4333-8333-333333333333";
    const retained = { id, name: "Company unavailable", slug: "", icon: null, unavailable: true };
    mocks.readCompanyReferences.mockResolvedValue([retained]);
    await getSessionWatchlistPageData({
      sessionWatchlistId, locale: "en", intent: { kind: "create", draft: {
        title: "Restricted", companyIds: [id], filters: {}, isPublic: false,
      } },
    });
    expect(mocks.buildWatchlistPageData).toHaveBeenCalledWith(expect.objectContaining({
      detail: expect.objectContaining({ companies: [retained], filters: {} }),
    }));
  });

  it("materializes a shared clone as the same editable local draft shape", async () => {
    mocks.getSharedWatchlistById.mockResolvedValue({
      id: sourceWatchlistId,
      title: "Finance",
      filters: { occupationSlugs: ["finance"] },
      companies: [{ id: "company-1", name: "Acme", slug: "acme", icon: null }],
    });

    const result = await getSessionWatchlistPageData({
      sessionWatchlistId,
      locale: "en",
      intent: { kind: "clone", watchlistId: sourceWatchlistId, title: "Finance copy" },
    });

    expect(result).toEqual({
      data: pageData,
      draft: {
        title: "Finance copy",
        companyIds: ["company-1"],
        filters: { occupationSlugs: ["finance"] },
        isPublic: false,
      },
    });
    expect(mocks.buildWatchlistPageData).toHaveBeenCalledWith(expect.objectContaining({
      detail: expect.objectContaining({
        id: sessionWatchlistId,
        title: "Finance copy",
        companies: [{ id: "company-1", name: "Acme", slug: "acme", icon: null }],
      }),
    }));
  });

  it("rejects malformed browser state before querying services", async () => {
    await expect(getSessionWatchlistPageData({
      sessionWatchlistId: "not-a-watchlist-id",
      locale: "en",
      intent: {
        kind: "create",
        draft: { title: "", companyIds: [], filters: {}, isPublic: false },
      },
    })).resolves.toEqual({ error: "invalid_input" });

    expect(mocks.getViewerJobLanguages).not.toHaveBeenCalled();
    expect(mocks.buildWatchlistPageData).not.toHaveBeenCalled();
  });

  it("loads browser-backed overview activity through the shared batch resolver", async () => {
    const intent = {
      kind: "create" as const,
      draft: {
        title: "Swiss internships",
        companyIds: [],
        filters: { anyCompany: true, locationSlugs: ["switzerland"] },
        isPublic: false as const,
      },
    };

    await expect(getSessionWatchlistActivityPreviews({
      entries: [{ id: sessionWatchlistId, intent }],
      locale: "en",
    })).resolves.toEqual({
      previews: {
        [sessionWatchlistId]: {
          activeJobCount: 24,
          activeCompanyCount: 3,
          topCompanies: [],
        },
      },
    });

    expect(mocks.getWatchlistActivityPreviewsForDrafts).toHaveBeenCalledWith(
      [{
        id: sessionWatchlistId,
        filters: intent.draft.filters,
        companyIds: [],
      }],
      "en",
      ["en"],
    );
  });
});
