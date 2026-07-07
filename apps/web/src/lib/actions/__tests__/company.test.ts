import { beforeEach, describe, expect, it, vi } from "vitest";

const service = vi.hoisted(() => ({
  suggestCompanies: vi.fn(),
  searchCompaniesForWatchlist: vi.fn(),
  suggestIndustries: vi.fn(),
  getCompanyBySlug: vi.fn(),
  getSimilarCompanies: vi.fn(),
  getCompanyPostings: vi.fn(),
  getCompanyPostingsAnonymous: vi.fn(),
  getCompanyTopLocations: vi.fn(),
  getCompanyLocationsGrouped: vi.fn(),
  getCompanyLocationsGroupedWithMacros: vi.fn(),
}));

vi.mock("@/lib/services/company", () => service);

import * as actions from "../company";

type CompanyActionName = keyof typeof service & keyof typeof actions;

const cases: Array<{
  name: CompanyActionName;
  args: unknown[];
  result: unknown;
}> = [
  { name: "suggestCompanies", args: [{ query: "ac" }], result: [{ id: "co-1" }] },
  {
    name: "searchCompaniesForWatchlist",
    args: [{ locale: "en", offset: 0, limit: 10 }],
    result: { companies: [], total: 0 },
  },
  { name: "suggestIndustries", args: [{ query: "soft", locale: "en" }], result: [] },
  { name: "getCompanyBySlug", args: ["acme", "en"], result: { slug: "acme" } },
  {
    name: "getSimilarCompanies",
    args: ["co-1", 7, { locale: "en" }],
    result: { companies: [], hasMore: false },
  },
  { name: "getCompanyPostings", args: [{ companyId: "co-1", locale: "en" }], result: [] },
  {
    name: "getCompanyPostingsAnonymous",
    args: [{ companyId: "co-1", locale: "en" }],
    result: { postings: [], hasMore: false },
  },
  { name: "getCompanyTopLocations", args: ["co-1", "en"], result: [] },
  { name: "getCompanyLocationsGrouped", args: ["co-1", "en"], result: [] },
  { name: "getCompanyLocationsGroupedWithMacros", args: ["co-1", "en"], result: [] },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("company server actions", () => {
  it.each(cases)("delegates $name to the company service", async ({ name, args, result }) => {
    service[name].mockResolvedValue(result);

    const out = await actions[name](...(args as never[]));

    expect(out).toBe(result);
    expect(service[name]).toHaveBeenCalledWith(...args);
  });
});
