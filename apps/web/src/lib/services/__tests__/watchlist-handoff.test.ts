import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getCompanyIdsBySlugs: vi.fn(),
  createWatchlist: vi.fn(),
}));

vi.mock("@/lib/services/company-detail", () => ({
  getCompanyIdsBySlugs: mocks.getCompanyIdsBySlugs,
}));

vi.mock("@/lib/services/watchlists", () => ({
  createWatchlist: mocks.createWatchlist,
}));

import { createWatchlistFromHandoffWithDeps } from "../watchlist-handoff";

describe("createWatchlistFromHandoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createWatchlist.mockResolvedValue({ id: "watchlist-1", slug: "roles" });
  });

  it("resolves and deduplicates public company slugs before the UUID write", async () => {
    mocks.getCompanyIdsBySlugs.mockResolvedValue(new Map([
      ["stripe", "uuid-stripe"],
      ["gitlab", "uuid-gitlab"],
    ]));

    await expect(createWatchlistFromHandoffWithDeps({
      title: "Roles",
      description: "Selected companies",
      companySlugs: [" Stripe ", "gitlab", "stripe"],
      filters: { workMode: ["remote"] },
    }, mocks)).resolves.toEqual({ id: "watchlist-1", slug: "roles" });

    expect(mocks.getCompanyIdsBySlugs).toHaveBeenCalledWith([
      "stripe",
      "gitlab",
    ]);
    expect(mocks.createWatchlist).toHaveBeenCalledWith({
      title: "Roles",
      description: "Selected companies",
      companyIds: ["uuid-stripe", "uuid-gitlab"],
      filters: { workMode: ["remote"], anyCompany: false },
    });
  });

  it("fails closed when any requested company slug is unknown", async () => {
    mocks.getCompanyIdsBySlugs.mockResolvedValue(new Map([
      ["stripe", "uuid-stripe"],
    ]));

    await expect(createWatchlistFromHandoffWithDeps({
      title: "Roles",
      companySlugs: ["stripe", "missing"],
    }, mocks)).resolves.toEqual({ error: "invalid_companies" });
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });

  it("propagates the account-wide limit from the atomic create path", async () => {
    mocks.getCompanyIdsBySlugs.mockResolvedValue(new Map());
    mocks.createWatchlist.mockResolvedValue({ error: "limit_reached" });

    await expect(createWatchlistFromHandoffWithDeps({
      title: "Eleventh",
      companySlugs: [],
    }, mocks)).resolves.toEqual({ error: "limit_reached" });
  });
});
