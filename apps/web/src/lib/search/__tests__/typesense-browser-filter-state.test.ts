import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  invalidate: vi.fn(),
}));

vi.mock("@/lib/search/typesense-browser-key", () => ({
  getTypesenseBrowserConfig: mocks.getConfig,
  invalidateTypesenseBrowserConfigIfUnauthorized: mocks.invalidate,
}));

import {
  parseCompanyFilterStateOffline,
  resolveCompanyFilterStateDirect,
} from "../typesense-browser-filter-state";

const config = {
  protocol: "https",
  host: "typesense.example.test",
  port: 443,
  apiKey: "scoped-key",
  expiresAt: Date.now() + 60_000,
};

beforeEach(() => {
  mocks.getConfig.mockReset();
  mocks.getConfig.mockResolvedValue(config);
  mocks.invalidate.mockReset();
  vi.unstubAllGlobals();
});

describe("resolveCompanyFilterStateDirect", () => {
  it("resolves bounded canonical slugs in one scoped multi_search", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          results: [
            {
              hits: [
                {
                  document: {
                    location_id: 10,
                    slug: "zurich",
                    type: "city",
                    parent_name: "Switzerland",
                    name_en: "Zurich",
                    name_de: "Zürich",
                  },
                },
              ],
            },
            {
              hits: [
                {
                  document: {
                    occupation_id: 20,
                    slug: "software-engineer",
                    name: "Software Engineer",
                    locale: "en",
                  },
                },
                {
                  document: {
                    occupation_id: 20,
                    slug: "software-engineer",
                    name: "Softwareentwickler/in",
                    locale: "de",
                  },
                },
              ],
            },
            {
              hits: [
                {
                  document: {
                    seniority_id: 30,
                    slug: "senior",
                    name: "Senior",
                    locale: "de",
                  },
                },
              ],
            },
            {
              hits: [
                {
                  document: {
                    technology_id: 40,
                    slug: "react",
                    name: "React",
                  },
                },
              ],
            },
          ],
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolveCompanyFilterStateDirect(
      new URLSearchParams(
        "loc=zurich&occ=software-engineer&sen=senior&tech=react&wm=remote&etype=full_time",
      ),
      "de",
    );

    expect(result).toEqual({
      complete: true,
      parsed: {
        keywords: [],
        locations: [
          {
            id: 10,
            slug: "zurich",
            name: "Zürich",
            type: "city",
            parentName: "Switzerland",
          },
        ],
        occupations: [
          {
            id: 20,
            slug: "software-engineer",
            name: "Softwareentwickler/in",
          },
        ],
        seniorities: [{ id: 30, slug: "senior", name: "Senior" }],
        technologies: [{ id: 40, slug: "react", name: "React" }],
        workMode: ["remote"],
        employmentTypes: ["full_time"],
      },
    });
    expect(mocks.getConfig).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://typesense.example.test:443/multi_search");
    expect(init.headers).toMatchObject({ "x-typesense-api-key": "scoped-key" });
    const body = JSON.parse(String(init.body));
    expect(body.searches.map((search: { collection: string }) => search.collection)).toEqual([
      "location",
      "occupation",
      "seniority",
      "technology",
    ]);
    expect(body.searches[0].filter_by).toBe("slug:[`zurich`]");
  });

  it("fails closed when an explicit slug cannot be resolved", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ results: [{ hits: [] }] }), {
          status: 200,
        }),
      ),
    );

    const result = await resolveCompanyFilterStateDirect(
      new URLSearchParams("loc=missing-place"),
      "en",
    );

    expect(result.complete).toBe(false);
    expect(result.parsed.unresolvedExplicitSlugs).toEqual({
      loc: ["missing-place"],
    });
  });

  it("rejects malformed HTTP-200 documents instead of constructing invalid filters", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            results: [{ hits: [{ document: { slug: "zurich", type: "city" } }] }],
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(
      resolveCompanyFilterStateDirect(
        new URLSearchParams("loc=zurich"),
        "en",
      ),
    ).rejects.toThrow("malformed location");
  });

  it("rejects malformed HTTP-200 result envelopes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ results: [{}] }), { status: 200 }),
      ),
    );

    await expect(
      resolveCompanyFilterStateDirect(
        new URLSearchParams("tech=react"),
        "en",
      ),
    ).rejects.toThrow("malformed hits");
  });

  it("rejects oversized or unsafe input before requesting a child key", async () => {
    vi.stubGlobal("fetch", vi.fn());

    const result = await resolveCompanyFilterStateDirect(
      new URLSearchParams(`loc=${"x".repeat(101)}`),
      "en",
    );

    expect(result.complete).toBe(false);
    expect(mocks.getConfig).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps network-free degraded state bounded and explicitly unresolved", () => {
    const result = parseCompanyFilterStateOffline(
      new URLSearchParams(`q=${"x".repeat(600)}&loc=zurich`),
    );

    expect(result.complete).toBe(false);
    expect(result.parsed.keywords).toEqual([]);
    expect(result.parsed.unresolvedExplicitSlugs).toEqual({ loc: ["zurich"] });
  });

  it("refuses to reinterpret semantic free text as title keywords", async () => {
    vi.stubGlobal("fetch", vi.fn());

    const result = await resolveCompanyFilterStateDirect(
      new URLSearchParams("q=remote"),
      "en",
    );

    expect(result.complete).toBe(false);
    expect(mocks.getConfig).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("invalidates a rejected scoped key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("unauthorized", { status: 401 })),
    );

    await expect(
      resolveCompanyFilterStateDirect(
        new URLSearchParams("tech=react"),
        "en",
      ),
    ).rejects.toThrow("401");
    expect(mocks.invalidate).toHaveBeenCalledWith(401);
  });
});
