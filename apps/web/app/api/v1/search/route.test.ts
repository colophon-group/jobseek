import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// The route imports `server-only`-marked modules via `@/lib/cache-ttl` and
// the search actions. The admin-route tests use the same shim pattern.
vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  apiLimit: vi.fn(),
  logExternalError: vi.fn(),
  parseSearchFilters: vi.fn(),
  searchJobs: vi.fn(),
  listTopCompanies: vi.fn(),
}));

// Avoid touching Redis / Upstash from a unit test. The default rejection
// exercises the route's documented fail-open rate-limit behavior; individual
// tests can override it to cover 429 responses.
vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: { limit: mocks.apiLimit },
  getClientIp: () => "test-ip",
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

// Route handler now imports from `@/lib/services/*` (issue #3231). The
// `@/lib/actions/*` re-export wrappers still exist for UI callers, but
// the route does not touch them — mock the services here so we test the
// real handler graph.
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
  return new NextRequest(`http://localhost/api/v1/search${qs}`);
}

async function callRoute(qs: string) {
  const res = await GET(makeReq(qs));
  const body = (await res.json()) as Record<string, unknown>;
  return { res, body };
}

describe("GET /api/v1/search", () => {
  beforeEach(() => {
    mocks.apiLimit.mockReset();
    mocks.apiLimit.mockRejectedValue(new Error("no redis in unit tests"));
    mocks.logExternalError.mockReset();
    mocks.parseSearchFilters.mockReset();
    mocks.parseSearchFilters.mockResolvedValue(emptyParsed);
    mocks.searchJobs.mockReset();
    mocks.searchJobs.mockResolvedValue(emptyResult);
    mocks.listTopCompanies.mockReset();
    mocks.listTopCompanies.mockResolvedValue(emptyResult);
  });

  it("defaults `languages` to `[]` (no filter) when `lang=` is absent", async () => {
    mocks.apiLimit.mockResolvedValueOnce({
      success: true,
      limit: 30,
      remaining: 29,
      reset: Date.now() + 30_000,
    });
    const { res } = await callRoute("?locale=en");
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe(
      "public, max-age=0, must-revalidate",
    );
    expect(res.headers.get("Vercel-CDN-Cache-Control")).toBe(
      "public, max-age=300",
    );
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("X-RateLimit-Limit")).toBeNull();
    expect(res.headers.get("X-RateLimit-Remaining")).toBeNull();
    expect(res.headers.get("X-RateLimit-Reset")).toBeNull();
    // No keywords → listTopCompanies path
    expect(mocks.listTopCompanies).toHaveBeenCalledTimes(1);
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.languages).toEqual([]);
    expect(call.locale).toBe("en");
    expect(call.salaryMinEur).toBeUndefined();
    expect(call.salaryMaxEur).toBeUndefined();
    expect(call.experienceMin).toBeUndefined();
    expect(call.experienceMax).toBeUndefined();
  });

  it("passes a single-value `lang=de` through as `languages: ['de']`", async () => {
    await callRoute("?locale=en&lang=de");
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.languages).toEqual(["de"]);
    // UI locale stays distinct from job-document language.
    expect(call.locale).toBe("en");
  });

  it("parses a comma-separated `lang=de,fr` into a sorted multi-value filter", async () => {
    await callRoute("?locale=en&lang=de,fr");
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.languages).toEqual(["de", "fr"]);
  });

  it("dedupes repeated `lang=` codes", async () => {
    await callRoute("?locale=en&lang=de,de,fr");
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.languages).toEqual(["de", "fr"]);
  });

  it("trims whitespace around comma-separated `lang=` codes", async () => {
    await callRoute("?locale=en&lang=de%20%2C%20fr");
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.languages).toEqual(["de", "fr"]);
  });

  it.each([
    {
      caseName: "unsupported UI locale",
      query: "?locale=es",
      error: "Invalid 'locale' param. Supported: en, de, fr, it",
    },
    {
      caseName: "unknown work mode",
      query: "?locale=en&wm=remote,bogus",
      error: "Invalid 'wm' value(s): bogus. Supported: onsite, hybrid, remote",
    },
    {
      caseName: "unknown employment type",
      query: "?locale=en&etype=full_time,bogus",
      error:
        "Invalid 'etype' value(s): bogus. Supported: full_time, part_time, contract, internship, temporary, volunteer",
    },
    {
      caseName: "unknown document language",
      query: "?locale=en&lang=xx",
      error: "Invalid 'lang' value(s): xx. Supported: en, de, fr, it",
    },
    {
      caseName: "UI-only all-language sentinel",
      query: "?locale=en&lang=*",
      error: "Invalid 'lang' value(s): *. Supported: en, de, fr, it",
    },
    {
      caseName: "empty document-language array",
      query: "?locale=en&lang=",
      error:
        "Invalid 'lang' param: must be a comma-separated list of language codes (en, de, fr, it)",
    },
    {
      caseName: "mixed valid and invalid document-language array",
      query: "?locale=en&lang=en,xx",
      error: "Invalid 'lang' value(s): xx. Supported: en, de, fr, it",
    },
    {
      caseName: "malformed salary range",
      query: "?locale=en&sal=abc-def",
      error: "Invalid 'sal' param: bounds must be non-negative integers",
    },
    {
      caseName: "malformed experience range",
      query: "?locale=en&exp=3-nope",
      error: "Invalid 'exp' param: bounds must be non-negative integers",
    },
    {
      caseName: "reversed salary range",
      query: "?locale=en&sal=200000-100000",
      error: "Invalid 'sal' param: min cannot be greater than max",
    },
    {
      caseName: "unsafe integer bound",
      query: "?locale=en&exp=9007199254740992-",
      error: "Invalid 'exp' param: bounds must be non-negative integers",
    },
    {
      caseName: "empty salary range",
      query: "?locale=en&sal=",
      error: "Invalid 'sal' param: expected min-max",
    },
    {
      caseName: "salary range without either bound",
      query: "?locale=en&sal=-",
      error: "Invalid 'sal' param: at least one bound is required",
    },
    {
      caseName: "salary range without a separator",
      query: "?locale=en&sal=100000",
      error: "Invalid 'sal' param: expected min-max",
    },
    {
      caseName: "negative salary bound",
      query: "?locale=en&sal=-1-100000",
      error: "Invalid 'sal' param: expected min-max",
    },
    {
      caseName: "empty experience range",
      query: "?locale=en&exp=",
      error: "Invalid 'exp' param: expected min-max",
    },
    {
      caseName: "experience range without either bound",
      query: "?locale=en&exp=-",
      error: "Invalid 'exp' param: at least one bound is required",
    },
    {
      caseName: "experience range without a separator",
      query: "?locale=en&exp=3",
      error: "Invalid 'exp' param: expected min-max",
    },
    {
      caseName: "negative experience bound",
      query: "?locale=en&exp=-1-5",
      error: "Invalid 'exp' param: expected min-max",
    },
  ])("returns the documented 400 contract for $caseName", async ({ query, error }) => {
    const { res, body } = await callRoute(query);

    expect(res.status).toBe(400);
    expect(body).toEqual({ error });
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
  });

  it("preserves successful rate-limit metadata on validation errors", async () => {
    const reset = Date.now() + 30_000;
    mocks.apiLimit.mockResolvedValueOnce({
      success: true,
      limit: 30,
      remaining: 29,
      reset,
    });

    const { res, body } = await callRoute("?locale=en&sal=-");

    expect(res.status).toBe(400);
    expect(body).toEqual({
      error: "Invalid 'sal' param: at least one bound is required",
    });
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("X-RateLimit-Limit")).toBe("30");
    expect(res.headers.get("X-RateLimit-Remaining")).toBe("29");
    expect(res.headers.get("X-RateLimit-Reset")).toBe(String(reset));
  });

  it("uses the `searchJobs` path when `q=` is set, still forwarding `languages`", async () => {
    mocks.parseSearchFilters.mockResolvedValue({
      ...emptyParsed,
      keywords: ["engineer"],
    });
    await callRoute("?locale=en&q=engineer&lang=de");
    expect(mocks.searchJobs).toHaveBeenCalledTimes(1);
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    const call = mocks.searchJobs.mock.calls[0][0];
    expect(call.keywords).toEqual(["engineer"]);
    expect(call.languages).toEqual(["de"]);
  });

  it("includes `lang=` in the `moreAt` URL when present", async () => {
    const { body } = await callRoute("?locale=en&q=engineer&lang=de,fr");
    expect(typeof body.moreAt).toBe("string");
    const moreAt = body.moreAt as string;
    expect(moreAt).toContain("/en/explore");
    const url = new URL(moreAt);
    expect(url.searchParams.get("lang")).toBe("de,fr");
    expect(url.searchParams.get("q")).toBe("engineer");
  });

  it("pins `moreAt` to all languages when the REST caller omits `lang`", async () => {
    const { body } = await callRoute("?locale=en&q=engineer");
    const url = new URL(body.moreAt as string);
    expect(url.searchParams.get("lang")).toBe("*");
    expect(url.searchParams.get("q")).toBe("engineer");
  });

  it.each([
    {
      caseName: "closed ranges",
      query: "?locale=en&sal=90000-140000&exp=3-7",
      salaryMinEur: 90000,
      salaryMaxEur: 140000,
      experienceMin: 3,
      experienceMax: 7,
    },
    {
      caseName: "minimum-only open ranges",
      query: "?locale=en&sal=90000-&exp=3-",
      salaryMinEur: 90000,
      salaryMaxEur: undefined,
      experienceMin: 3,
      experienceMax: undefined,
    },
    {
      caseName: "maximum-only open ranges",
      query: "?locale=en&sal=-140000&exp=-7",
      salaryMinEur: undefined,
      salaryMaxEur: 140000,
      experienceMin: undefined,
      experienceMax: 7,
    },
  ])(
    "forwards valid $caseName as numbers",
    async ({
      query,
      salaryMinEur,
      salaryMaxEur,
      experienceMin,
      experienceMax,
    }) => {
      await callRoute(query);
      const call = mocks.listTopCompanies.mock.calls[0][0];
      expect(call.salaryMinEur).toBe(salaryMinEur);
      expect(call.salaryMaxEur).toBe(salaryMaxEur);
      expect(call.experienceMin).toBe(experienceMin);
      expect(call.experienceMax).toBe(experienceMax);
    },
  );

  it("forwards `wm=remote` into `moreAt` (regression for lost work-mode param)", async () => {
    // The API already accepted `wm` and forwarded it to searchJobs, but
    // the moreAt-URL builder was dropping it (#3230 audit). After this
    // fix the round-trip is intact.
    mocks.parseSearchFilters.mockResolvedValue({
      ...emptyParsed,
      workMode: ["remote"],
    });
    const { body } = await callRoute("?locale=en&wm=remote");
    const url = new URL(body.moreAt as string);
    expect(url.searchParams.get("wm")).toBe("remote");
  });

  it("forwards `etype=` to the parser, search filters, and `moreAt`", async () => {
    mocks.parseSearchFilters.mockResolvedValue({
      ...emptyParsed,
      keywords: ["designer"],
      employmentTypes: ["full_time", "internship"],
    });

    const { body } = await callRoute("?locale=en&q=designer&etype=full_time,internship");

    expect(mocks.parseSearchFilters).toHaveBeenCalledWith(
      expect.objectContaining({
        q: "designer",
        etype: "full_time,internship",
      }),
    );
    expect(mocks.searchJobs).toHaveBeenCalledTimes(1);
    const call = mocks.searchJobs.mock.calls[0][0];
    expect(call.employmentTypes).toEqual(["full_time", "internship"]);

    const url = new URL(body.moreAt as string);
    expect(url.searchParams.get("etype")).toBe("full_time,internship");
    expect(url.searchParams.get("q")).toBe("designer");
  });

  it("returns 400 when an exact slug cannot be resolved", async () => {
    mocks.parseSearchFilters.mockResolvedValue({
      ...emptyParsed,
      unresolvedExplicitSlugs: { loc: ["not-a-location"] },
    });

    const { res, body } = await callRoute("?locale=en&loc=not-a-location");

    expect(res.status).toBe(400);
    expect(body).toEqual({
      error:
        "Invalid 'loc' slug(s): not-a-location. Use /api/v1/resolve for exact slugs.",
    });
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
  });

  it("pins salary-bearing `moreAt` links to EUR and drops caller salcur", async () => {
    const { body } = await callRoute(
      "?locale=en&q=engineer&sal=100000-&salcur=USD",
    );
    const url = new URL(body.moreAt as string);

    expect(url.searchParams.get("sal")).toBe("100000-");
    expect(url.searchParams.get("salcur")).toBe("EUR");
  });

  it("keeps rate-limit failures distinct from validation failures", async () => {
    const reset = Date.now() + 30_000;
    mocks.apiLimit.mockResolvedValueOnce({
      success: false,
      limit: 30,
      remaining: 0,
      reset,
    });

    const { res, body } = await callRoute("?locale=en&lang=xx");

    expect(res.status).toBe(429);
    expect(body).toEqual({ error: "Too many requests" });
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("Retry-After")).not.toBeNull();
    expect(res.headers.get("X-RateLimit-Limit")).toBe("30");
    expect(res.headers.get("X-RateLimit-Remaining")).toBe("0");
    expect(res.headers.get("X-RateLimit-Reset")).toBe(String(reset));
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });

  it("returns a safe 500 body when the search provider fails", async () => {
    const reset = Date.now() + 30_000;
    mocks.apiLimit.mockResolvedValueOnce({
      success: true,
      limit: 30,
      remaining: 28,
      reset,
    });
    const providerError = Object.assign(
      new Error("provider-internal-canary-do-not-expose"),
      { status: 503 },
    );
    mocks.listTopCompanies.mockRejectedValueOnce(providerError);

    const { res, body } = await callRoute("?locale=de");

    expect(res.status).toBe(500);
    expect(body).toEqual({ error: "Search service unavailable" });
    expect(JSON.stringify(body)).not.toContain("provider-internal-canary");
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("X-RateLimit-Limit")).toBe("30");
    expect(res.headers.get("X-RateLimit-Remaining")).toBe("28");
    expect(res.headers.get("X-RateLimit-Reset")).toBe(String(reset));
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      { service: "typesense", operation: "public_api_search" },
      providerError,
    );
  });
});
