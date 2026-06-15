import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// The route imports `server-only`-marked modules via `@/lib/cache-ttl` and
// the search actions. The admin-route tests use the same shim pattern.
vi.mock("server-only", () => ({}));

// Avoid touching Redis / Upstash from a unit test — `checkRateLimit`
// degrades closed when the limiter throws.
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

describe("GET /api/v1/search — lang= param (issue #3230)", () => {
  beforeEach(() => {
    mocks.parseSearchFilters.mockReset();
    mocks.parseSearchFilters.mockResolvedValue(emptyParsed);
    mocks.searchJobs.mockReset();
    mocks.searchJobs.mockResolvedValue(emptyResult);
    mocks.listTopCompanies.mockReset();
    mocks.listTopCompanies.mockResolvedValue(emptyResult);
  });

  it("defaults `languages` to `[]` (no filter) when `lang=` is absent", async () => {
    const { res } = await callRoute("?locale=en");
    expect(res.status).toBe(200);
    // No keywords → listTopCompanies path
    expect(mocks.listTopCompanies).toHaveBeenCalledTimes(1);
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.languages).toEqual([]);
    expect(call.locale).toBe("en");
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

  it("rejects `lang=xx` (unknown code) with an error response", async () => {
    const { res, body } = await callRoute("?locale=en&lang=xx");
    expect(res.status).toBe(200); // apiResponse always 200 with error body
    expect(body.error).toMatch(/Invalid 'lang' value/);
    expect(body.error).toContain("xx");
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
  });

  it("rejects `lang=` (empty) with an error response", async () => {
    const { res, body } = await callRoute("?locale=en&lang=");
    expect(res.status).toBe(200);
    expect(body.error).toMatch(/Invalid 'lang' param/);
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });

  it("rejects a mixed-valid/invalid `lang=en,xx` with an error response", async () => {
    const { body } = await callRoute("?locale=en&lang=en,xx");
    expect(body.error).toMatch(/Invalid 'lang' value/);
    expect(body.error).toContain("xx");
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
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

  it("omits `lang=` from `moreAt` when not provided by the caller", async () => {
    const { body } = await callRoute("?locale=en&q=engineer");
    const url = new URL(body.moreAt as string);
    expect(url.searchParams.has("lang")).toBe(false);
    expect(url.searchParams.get("q")).toBe("engineer");
  });

  it("rejects malformed `sal=` instead of forwarding NaN", async () => {
    const { body } = await callRoute("?locale=en&sal=abc-def");
    expect(body.error).toMatch(/Invalid 'sal' param/);
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
  });

  it("rejects malformed `exp=` instead of building an invalid Typesense filter", async () => {
    const { body } = await callRoute("?locale=en&exp=3-nope");
    expect(body.error).toMatch(/Invalid 'exp' param/);
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
  });

  it("rejects reversed numeric ranges", async () => {
    const { body } = await callRoute("?locale=en&sal=200000-100000");
    expect(body.error).toMatch(/min cannot be greater than max/);
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });

  it("forwards valid `sal=` and `exp=` ranges as numbers", async () => {
    await callRoute("?locale=en&sal=90000-140000&exp=3-7");
    const call = mocks.listTopCompanies.mock.calls[0][0];
    expect(call.salaryMinEur).toBe(90000);
    expect(call.salaryMaxEur).toBe(140000);
    expect(call.experienceMin).toBe(3);
    expect(call.experienceMax).toBe(7);
  });

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
});
