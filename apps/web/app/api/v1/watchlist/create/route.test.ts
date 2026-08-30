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
  logExternalError: vi.fn(),
  parseSearchFilters: vi.fn(),
  searchJobs: vi.fn(),
  listTopCompanies: vi.fn(),
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
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
    mocks.logExternalError.mockReset();
    mocks.parseSearchFilters.mockReset();
    mocks.parseSearchFilters.mockResolvedValue(emptyParsed);
    mocks.searchJobs.mockReset();
    mocks.searchJobs.mockResolvedValue(emptyResult);
    mocks.listTopCompanies.mockReset();
    mocks.listTopCompanies.mockResolvedValue(emptyResult);
  });

  it("returns 400 when the required `title` param is missing (#3213)", async () => {
    const { res, body } = await callRoute("?locale=en");

    expect(res.status).toBe(400);
    expect(body.error).toBe("Missing required 'title' param");
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });

  it("rejects unsupported locales before parsing or linking filters", async () => {
    const { res, body } = await callRoute("?title=Design%20roles&locale=xx");

    expect(res.status).toBe(400);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(body.error).toBe("Invalid 'locale' param. Supported: en, de, fr, it");
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });

  it("rejects unsupported finite-list values before parsing filters", async () => {
    const { res, body } = await callRoute(
      "?locale=en&title=Roles&etype=contract,unknown",
    );

    expect(res.status).toBe(400);
    expect(body.error).toBe(
      "Invalid 'etype' value(s): unknown. Supported: full_time, part_time, contract, internship, temporary, volunteer",
    );
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
  });

  it("rejects unresolved exact slugs before running the preview search", async () => {
    mocks.parseSearchFilters.mockResolvedValue({
      ...emptyParsed,
      unresolvedExplicitSlugs: { tech: ["not-a-tech"] },
    });

    const { res, body } = await callRoute(
      "?locale=en&title=Roles&tech=not-a-tech",
    );

    expect(res.status).toBe(400);
    expect(body.error).toBe(
      "Invalid 'tech' slug(s): not-a-tech. Use /api/v1/resolve for exact slugs.",
    );
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
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

  it("does not cache a degraded preview as a real empty result", async () => {
    mocks.listTopCompanies.mockResolvedValue({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });

    const { res, body } = await callRoute("?locale=en&title=Roles");

    expect(res.status).toBe(500);
    expect(body).toEqual({ error: "Search service unavailable" });
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      {
        service: "typesense",
        operation: "public_api_watchlist_create_degraded",
      },
      expect.any(Error),
    );
  });

  it("returns a non-cacheable safe error when filter resolution fails", async () => {
    const providerError = new Error("provider-internal-canary-do-not-expose");
    mocks.parseSearchFilters.mockRejectedValue(providerError);

    const { res, body } = await callRoute("?locale=en&title=Roles");

    expect(res.status).toBe(500);
    expect(body).toEqual({ error: "Search service unavailable" });
    expect(JSON.stringify(body)).not.toContain("provider-internal-canary");
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      {
        service: "typesense",
        operation: "public_api_watchlist_create_filters",
      },
      providerError,
    );
  });
});
