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

import { CompanyReferenceError } from "@/lib/services/company-references";
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
      companySlugs: [" Stripe ", "gitlab", "stripe"],
      filters: { workMode: ["remote"] },
    }, mocks)).resolves.toEqual({ id: "watchlist-1", slug: "roles" });

    expect(mocks.getCompanyIdsBySlugs).toHaveBeenCalledWith([
      "stripe",
      "gitlab",
    ]);
    expect(mocks.createWatchlist).toHaveBeenCalledWith({
      title: "Roles",
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
    }, mocks)).resolves.toEqual({ error: "unknown_company" });
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });

  it("returns a safe retryable error without creating on provider failure", async () => {
    mocks.getCompanyIdsBySlugs.mockRejectedValue(new Error("secret provider details"));
    await expect(createWatchlistFromHandoffWithDeps({ title: "Roles", companySlugs: ["stripe"] }, mocks)).resolves.toEqual({ error: "company_lookup_unavailable" });
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });

  it("preserves a classified canonical identity conflict", async () => {
    mocks.getCompanyIdsBySlugs.mockRejectedValue(new CompanyReferenceError("company_identity_conflict"));
    await expect(createWatchlistFromHandoffWithDeps({ title: "Roles", companySlugs: ["stripe"] }, mocks)).resolves.toEqual({ error: "company_identity_conflict" });
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
