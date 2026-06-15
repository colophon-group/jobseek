import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: {
    limit: vi.fn(async () => {
      throw new Error("no redis in unit tests");
    }),
  },
  getClientIp: () => "test-ip",
}));

const mocks = vi.hoisted(() => ({
  parseSearchFilters: vi.fn(),
  searchJobs: vi.fn(),
  listTopCompanies: vi.fn(),
}));

vi.mock("@/lib/services/search-input", () => ({
  parseSearchFilters: mocks.parseSearchFilters,
}));
vi.mock("@/lib/services/search", () => ({
  searchJobs: mocks.searchJobs,
  listTopCompanies: mocks.listTopCompanies,
}));

import { GET } from "./route";

const emptyParsed = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: [],
  employmentTypes: [],
};

const emptyResult = { companies: [], totalCompanies: 0 };

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/watchlist/create${qs}`);
}

async function callRoute(qs: string) {
  const res = await GET(makeReq(qs));
  const body = (await res.json()) as Record<string, unknown>;
  return { res, body };
}

describe("GET /api/v1/watchlist/create — filter params", () => {
  beforeEach(() => {
    mocks.parseSearchFilters.mockReset();
    mocks.parseSearchFilters.mockResolvedValue(emptyParsed);
    mocks.searchJobs.mockReset();
    mocks.searchJobs.mockResolvedValue(emptyResult);
    mocks.listTopCompanies.mockReset();
    mocks.listTopCompanies.mockResolvedValue(emptyResult);
  });

  it("forwards `etype=` to the parser, preview search, and create URL", async () => {
    mocks.parseSearchFilters.mockResolvedValue({
      ...emptyParsed,
      keywords: ["designer"],
      employmentTypes: ["full_time", "internship"],
    });
    mocks.searchJobs.mockResolvedValue({
      companies: [{ activeMatches: 7 }],
      totalCompanies: 2,
    });

    const { body } = await callRoute(
      "?locale=en&title=Design%20roles&q=designer&etype=full_time,internship",
    );

    expect(mocks.parseSearchFilters).toHaveBeenCalledWith(
      expect.objectContaining({
        q: "designer",
        etype: "full_time,internship",
      }),
    );
    expect(mocks.searchJobs).toHaveBeenCalledTimes(1);
    const call = mocks.searchJobs.mock.calls[0][0];
    expect(call.employmentTypes).toEqual(["full_time", "internship"]);

    const url = new URL(body.url as string);
    expect(url.pathname).toBe("/en/watchlists");
    expect(url.searchParams.get("etype")).toBe("full_time,internship");
    expect(url.searchParams.get("q")).toBe("designer");
    expect(body.preview).toMatchObject({
      matchingCompanies: 2,
      matchingJobs: 7,
    });
  });
});
