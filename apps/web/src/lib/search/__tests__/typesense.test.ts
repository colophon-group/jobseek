import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type SearchCall = {
  collection: string;
  params: Record<string, unknown>;
};

const mocks = vi.hoisted(() => ({
  calls: [] as SearchCall[],
  browserCalls: [] as SearchCall[],
  search: vi.fn(),
  multiSearch: vi.fn(),
}));

vi.mock("../typesense-client", () => ({
  getSearchClient: () => ({
    multiSearch: { perform: mocks.multiSearch },
    collections: (collection: string) => ({
      documents: () => ({
        search: (params: Record<string, unknown>) => {
          mocks.calls.push({ collection, params });
          return mocks.search(collection, params);
        },
      }),
    }),
  }),
}));

import { TypesenseBrowserProvider } from "../typesense-browser";
import { clearTypesenseBrowserConfig } from "../typesense-browser-key";
import { resolveTypesenseCompany } from "../typesense-company";
import { POSTING_BASE_FILTER } from "../typesense-filters";
import { TypesenseSearchProvider } from "../typesense";

const NOW = Math.floor(Date.UTC(2026, 5, 19) / 1000);
const DAY = 86_400;

function postingHit(
  companyId: string,
  companyName: string,
  firstSeenAt: number,
  title = `${companyName} role`,
) {
  return {
    document: {
      id: `${companyId}-${firstSeenAt}`,
      company_id: companyId,
      company_name: companyName,
      company_slug: companyId,
      title,
      is_active: true,
      location_ids: [],
      location_names: [],
      location_types: [],
      location_geo_types: [],
      technology_ids: [],
      experience_min: -1,
      locales: ["en"],
      first_seen_at: firstSeenAt,
    },
  };
}

function companyHit(
  id: string,
  name: string,
  activePostingCount: number,
  yearPostingCount: number,
) {
  return {
    document: {
      id,
      name,
      slug: id,
      active_posting_count: activePostingCount,
      year_posting_count: yearPostingCount,
    },
  };
}

const freshPosting = postingHit("fresh-co", "Fresh Co", NOW);
const stalePosting = postingHit("stale-bigco", "Stale BigCo", NOW - 16 * DAY);
const olderRole = postingHit("mixed-co", "Mixed Co", NOW - 3 * DAY, "Older role");
const freshRole = postingHit("mixed-co", "Mixed Co", NOW - DAY, "Fresh role");

function freshnessGroupedResponse() {
  return {
    grouped_hits: [
      { group_key: ["fresh-co"], found: 1, hits: [freshPosting] },
      { group_key: ["stale-bigco"], found: 50_000, hits: [stalePosting] },
    ],
    facet_counts: [
      { field_name: "company_id", counts: [], stats: { total_values: 2 } },
    ],
  };
}

function companyResponse() {
  return {
    found: 2,
    hits: [
      companyHit("stale-bigco", "Stale BigCo", 50_000, 50_000),
      companyHit("fresh-co", "Fresh Co", 1, 1),
    ],
  };
}

function outOfOrderPostingsResponse() {
  return {
    grouped_hits: [
      {
        group_key: ["mixed-co"],
        found: 2,
        // Reproduces the screenshot class: an older posting appears above a
        // fresher one inside the same anonymous company card.
        hits: [olderRole, freshRole],
      },
    ],
    facet_counts: [
      { field_name: "company_id", counts: [], stats: { total_values: 1 } },
    ],
  };
}

function mixedCompanyResponse() {
  return {
    found: 1,
    hits: [companyHit("mixed-co", "Mixed Co", 2, 2)],
  };
}

function rankedCompanyHits(ids: string[]) {
  return ids.map((id, index) =>
    companyHit(id, `Company ${id}`, ids.length - index, ids.length - index),
  );
}

function groupedPostingsFor(ids: string[], missing = new Set<string>()) {
  return {
    grouped_hits: ids
      .filter((id) => !missing.has(id))
      .map((id, index) => ({
        group_key: [id],
        found: 1,
        hits: [postingHit(id, `Company ${id}`, NOW - index)],
      })),
  };
}

function companyIdsFromFilter(filter: unknown): string[] {
  const match = String(filter).match(/company_id:\[([^\]]+)\]/);
  return match?.[1]?.split(",") ?? [];
}

function blankCompanyPostingHit(companyId: string, title: string) {
  const hit = postingHit(companyId, "", NOW, title);
  hit.document.company_slug = "";
  return hit;
}

function blankCompanyGroupedResponse() {
  return {
    grouped_hits: [
      {
        group_key: ["company-a"],
        found: 4_867,
        hits: [blankCompanyPostingHit("company-a", "Retail Parts Pro Store 5175")],
      },
      {
        group_key: ["company-b"],
        found: 4_348,
        hits: [blankCompanyPostingHit("company-b", "Delivery Specialist - Hub")],
      },
    ],
  };
}

function blankCompanyFacetResponse() {
  return {
    facet_counts: [
      {
        field_name: "company_id",
        counts: [
          { value: "company-a", count: 4_867 },
          { value: "company-b", count: 4_348 },
        ],
        stats: { total_values: 2 },
      },
    ],
  };
}

function canonicalBlankCompanyResponse() {
  return {
    hits: [
      companyHit("company-a", "Advance Auto Parts", 4_867, 4_867),
      companyHit("company-b", "O'Reilly Auto Parts", 4_348, 4_348),
    ],
  };
}

beforeEach(() => {
  mocks.calls.length = 0;
  mocks.browserCalls.length = 0;
  mocks.search.mockReset();
  mocks.multiSearch.mockReset();
  clearTypesenseBrowserConfig();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TypesenseSearchProvider.listTopCompanies", () => {
  it("ranks the anonymous default surface by active company size", async () => {
    const provider = new TypesenseSearchProvider();

    mocks.search.mockImplementation(async (collection: string) => {
      if (collection === "job_posting") return freshnessGroupedResponse();
      if (collection === "company") return companyResponse();
      throw new Error(`unexpected collection: ${collection}`);
    });

    const result = await provider.listTopCompanies({
      languages: [],
      locale: "en",
      offset: 0,
      limit: 2,
    });

    expect(result.companies.map((c) => c.company.id)).toEqual([
      "stale-bigco",
      "fresh-co",
    ]);
    expect(result.companies.map((c) => c.activeMatches)).toEqual([50_000, 1]);
    expect(result.totalCompanies).toBe(2);
    expect(mocks.calls[0]).toMatchObject({
      collection: "company",
      params: {
        q: "*",
        filter_by: "active_posting_count:>0",
        sort_by: "active_posting_count:desc,year_posting_count:desc",
        offset: 0,
        limit: 2,
      },
    });
    expect(mocks.calls[1]).toMatchObject({
      collection: "job_posting",
      params: {
        q: "*",
        filter_by: `company_id:[stale-bigco,fresh-co] && ${POSTING_BASE_FILTER}`,
        group_by: "company_id",
        group_limit: 10,
        sort_by: "first_seen_at:desc",
        per_page: 2,
      },
    });
  });

  it("honors a non-page-aligned offset in the deterministic company ranking", async () => {
    const ids = Array.from({ length: 15 }, (_, index) => `company-${index + 1}`);
    const hits = rankedCompanyHits(ids);
    mocks.search.mockImplementation(
      async (collection: string, params: Record<string, unknown>) => {
        if (collection === "company") {
          const offset = Number(params.offset);
          const limit = Number(params.limit);
          return { found: ids.length, hits: hits.slice(offset, offset + limit) };
        }
        return groupedPostingsFor(companyIdsFromFilter(params.filter_by));
      },
    );

    const result = await new TypesenseSearchProvider().listTopCompanies({
      languages: [],
      locale: "en",
      offset: 5,
      limit: 4,
    });

    expect(result.companies.map((entry) => entry.company.id)).toEqual(
      ids.slice(5, 9),
    );
    expect(mocks.calls[0]).toMatchObject({
      collection: "company",
      params: { offset: 0, limit: 9 },
    });
  });

  it("continues ranked hydration when stale company counts underfill a batch", async () => {
    const ids = ["stale", "company-b", "company-c", "company-d"];
    const hits = rankedCompanyHits(ids);
    const missing = new Set(["stale"]);
    mocks.search.mockImplementation(
      async (collection: string, params: Record<string, unknown>) => {
        if (collection === "company") {
          const offset = Number(params.offset);
          const limit = Number(params.limit);
          return { found: ids.length, hits: hits.slice(offset, offset + limit) };
        }
        return groupedPostingsFor(
          companyIdsFromFilter(params.filter_by),
          missing,
        );
      },
    );

    const result = await new TypesenseSearchProvider().listTopCompanies({
      languages: [],
      locale: "en",
      offset: 1,
      limit: 2,
    });

    expect(result.companies.map((entry) => entry.company.id)).toEqual([
      "company-c",
      "company-d",
    ]);
    expect(
      mocks.calls
        .filter((call) => call.collection === "company")
        .map((call) => call.params),
    ).toEqual([
      expect.objectContaining({ offset: 0, limit: 3 }),
      expect.objectContaining({ offset: 3, limit: 2 }),
    ]);
  });

  it("orders postings inside anonymous default company cards by freshness", async () => {
    const provider = new TypesenseSearchProvider();

    mocks.search.mockImplementation(async (collection: string) => {
      if (collection === "job_posting") return outOfOrderPostingsResponse();
      if (collection === "company") return mixedCompanyResponse();
      throw new Error(`unexpected collection: ${collection}`);
    });

    const result = await provider.listTopCompanies({
      languages: [],
      locale: "en",
      offset: 0,
      limit: 1,
    });

    expect(result.companies[0].postings.map((p) => p.title)).toEqual([
      "Fresh role",
      "Older role",
    ]);
  });

  it("uses canonical metadata for multiple filtered groups with blank posting metadata", async () => {
    const provider = new TypesenseSearchProvider();

    mocks.search.mockImplementation(
      async (collection: string, params: Record<string, unknown>) => {
        if (collection === "company") return canonicalBlankCompanyResponse();
        if (params.group_by === "company_id") {
          return blankCompanyGroupedResponse();
        }
        if (String(params.filter_by).includes("first_seen_at:>")) {
          return blankCompanyFacetResponse();
        }
        return blankCompanyFacetResponse();
      },
    );

    const result = await provider.listTopCompanies({
      languages: ["en"],
      locale: "en",
      offset: 0,
      limit: 2,
    });

    expect(result.companies.map((entry) => entry.company)).toEqual([
      expect.objectContaining({
        id: "company-a",
        name: "Advance Auto Parts",
        slug: "company-a",
      }),
      expect.objectContaining({
        id: "company-b",
        name: "O'Reilly Auto Parts",
        slug: "company-b",
      }),
    ]);
  });
});

describe("TypesenseBrowserProvider.listTopCompanies", () => {
  it("uses the same active-company ranking as the server provider", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url === "/api/typesense-key") {
          return Response.json({
            apiKey: "browser-key",
            host: "typesense.example",
            port: 443,
            protocol: "https",
            expiresAt: Date.now() + 60_000,
          });
        }

        const parsed = new URL(url);
        const collection = parsed.pathname.match(/\/collections\/([^/]+)/)?.[1];
        if (!collection) throw new Error(`unexpected URL: ${url}`);
        mocks.browserCalls.push({
          collection,
          params: Object.fromEntries(parsed.searchParams.entries()),
        });
        if (collection === "job_posting") return Response.json(freshnessGroupedResponse());
        if (collection === "company") return Response.json(companyResponse());
        throw new Error(`unexpected collection: ${collection}`);
      }),
    );

    const provider = new TypesenseBrowserProvider();
    const result = await provider.listTopCompanies({
      languages: [],
      locale: "en",
      offset: 0,
      limit: 2,
    });

    expect(result.companies.map((c) => c.company.id)).toEqual([
      "stale-bigco",
      "fresh-co",
    ]);
    expect(result.totalCompanies).toBe(2);
    expect(mocks.browserCalls[0]).toMatchObject({
      collection: "company",
      params: {
        q: "*",
        filter_by: "active_posting_count:>0",
        sort_by: "active_posting_count:desc,year_posting_count:desc",
        offset: "0",
        limit: "2",
      },
    });
    expect(mocks.browserCalls[1]).toMatchObject({
      collection: "job_posting",
      params: {
        q: "*",
        filter_by: `company_id:[stale-bigco,fresh-co] && ${POSTING_BASE_FILTER}`,
        group_by: "company_id",
        group_limit: "10",
        sort_by: "first_seen_at:desc",
        per_page: "2",
      },
    });
  });

  it("honors a non-page-aligned browser offset", async () => {
    const ids = Array.from({ length: 15 }, (_, index) => `company-${index + 1}`);
    const hits = rankedCompanyHits(ids);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url === "/api/typesense-key") {
          return Response.json({
            apiKey: "browser-key",
            host: "typesense.example",
            port: 443,
            protocol: "https",
            expiresAt: Date.now() + 60_000,
          });
        }
        const parsed = new URL(url);
        const collection = parsed.pathname.match(/\/collections\/([^/]+)/)?.[1];
        const params = Object.fromEntries(parsed.searchParams.entries());
        if (!collection) throw new Error(`unexpected URL: ${url}`);
        mocks.browserCalls.push({ collection, params });
        if (collection === "company") {
          const offset = Number(params.offset);
          const limit = Number(params.limit);
          return Response.json({
            found: ids.length,
            hits: hits.slice(offset, offset + limit),
          });
        }
        return Response.json(
          groupedPostingsFor(companyIdsFromFilter(params.filter_by)),
        );
      }),
    );

    const result = await new TypesenseBrowserProvider().listTopCompanies({
      languages: [],
      locale: "en",
      offset: 5,
      limit: 4,
    });

    expect(result.companies.map((entry) => entry.company.id)).toEqual(
      ids.slice(5, 9),
    );
    expect(mocks.browserCalls[0]).toMatchObject({
      collection: "company",
      params: { offset: "0", limit: "9" },
    });
  });

  it("continues browser hydration past an underfilled ranked batch", async () => {
    const ids = ["stale", "company-b", "company-c", "company-d"];
    const hits = rankedCompanyHits(ids);
    const missing = new Set(["stale"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url === "/api/typesense-key") {
          return Response.json({
            apiKey: "browser-key",
            host: "typesense.example",
            port: 443,
            protocol: "https",
            expiresAt: Date.now() + 60_000,
          });
        }
        const parsed = new URL(url);
        const collection = parsed.pathname.match(/\/collections\/([^/]+)/)?.[1];
        const params = Object.fromEntries(parsed.searchParams.entries());
        if (!collection) throw new Error(`unexpected URL: ${url}`);
        mocks.browserCalls.push({ collection, params });
        if (collection === "company") {
          const offset = Number(params.offset);
          const limit = Number(params.limit);
          return Response.json({
            found: ids.length,
            hits: hits.slice(offset, offset + limit),
          });
        }
        return Response.json(
          groupedPostingsFor(
            companyIdsFromFilter(params.filter_by),
            missing,
          ),
        );
      }),
    );

    const result = await new TypesenseBrowserProvider().listTopCompanies({
      languages: [],
      locale: "en",
      offset: 1,
      limit: 2,
    });

    expect(result.companies.map((entry) => entry.company.id)).toEqual([
      "company-c",
      "company-d",
    ]);
    expect(
      mocks.browserCalls
        .filter((call) => call.collection === "company")
        .map((call) => call.params),
    ).toEqual([
      expect.objectContaining({ offset: "0", limit: "3" }),
      expect.objectContaining({ offset: "3", limit: "2" }),
    ]);
  });

  it("orders browser-fetched anonymous card postings by freshness", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url === "/api/typesense-key") {
          return Response.json({
            apiKey: "browser-key",
            host: "typesense.example",
            port: 443,
            protocol: "https",
            expiresAt: Date.now() + 60_000,
          });
        }

        const parsed = new URL(url);
        const collection = parsed.pathname.match(/\/collections\/([^/]+)/)?.[1];
        if (!collection) throw new Error(`unexpected URL: ${url}`);
        if (collection === "job_posting") return Response.json(outOfOrderPostingsResponse());
        if (collection === "company") return Response.json(mixedCompanyResponse());
        throw new Error(`unexpected collection: ${collection}`);
      }),
    );

    const provider = new TypesenseBrowserProvider();
    const result = await provider.listTopCompanies({
      languages: [],
      locale: "en",
      offset: 0,
      limit: 1,
    });

    expect(result.companies[0].postings.map((p) => p.title)).toEqual([
      "Fresh role",
      "Older role",
    ]);
  });

  it("uses canonical metadata for multiple browser-fetched filtered groups", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url === "/api/typesense-key") {
          return Response.json({
            apiKey: "browser-key",
            host: "typesense.example",
            port: 443,
            protocol: "https",
            expiresAt: Date.now() + 60_000,
          });
        }

        const parsed = new URL(url);
        const collection = parsed.pathname.match(/\/collections\/([^/]+)/)?.[1];
        if (collection === "company") {
          return Response.json(canonicalBlankCompanyResponse());
        }
        if (parsed.searchParams.get("group_by") === "company_id") {
          return Response.json(blankCompanyGroupedResponse());
        }
        return Response.json(blankCompanyFacetResponse());
      }),
    );

    const provider = new TypesenseBrowserProvider();
    const result = await provider.listTopCompanies({
      languages: ["en"],
      locale: "en",
      offset: 0,
      limit: 2,
    });

    expect(result.companies.map((entry) => entry.company.name)).toEqual([
      "Advance Auto Parts",
      "O'Reilly Auto Parts",
    ]);
    expect(result.companies.every((entry) => entry.company.slug.length > 0)).toBe(
      true,
    );
  });
});

describe("loadPostingsWithCounts multi_search batching", () => {
  const params = {
    languages: ["en"],
    locale: "en",
    companyId: "fresh-co",
    keywords: ["engineer"],
    offset: 0,
    limit: 2,
  };

  function batchResponse() {
    return {
      results: [
        { found: 2, hits: [freshPosting, freshRole] },
        { found: 7 },
        { found: 11 },
      ],
    };
  }

  const malformedPostingSlots: Array<[string, unknown]> = [
    ["a truncated page", { found: 2, hits: [] }],
    ["a hit when found is zero", { found: 0, hits: [freshPosting] }],
    [
      "more hits than the requested page",
      { found: 3, hits: [freshPosting, freshRole, olderRole] },
    ],
    ["a structurally invalid posting", { found: 1, hits: [{ document: {} }] }],
  ];

  it("uses one ordered SDK batch for postings and both counts", async () => {
    mocks.multiSearch.mockResolvedValue(batchResponse());

    const result = await new TypesenseSearchProvider().loadPostingsWithCounts(params);

    expect(result).toMatchObject({ activeCount: 7, yearCount: 11 });
    expect(result.postings.map((posting) => posting.id)).toEqual([
      freshPosting.document.id,
      freshRole.document.id,
    ]);
    expect(mocks.multiSearch).toHaveBeenCalledOnce();
    const batch = mocks.multiSearch.mock.calls[0][0] as {
      searches: Array<Record<string, unknown>>;
    };
    expect(batch.searches).toHaveLength(3);
    expect(batch.searches.map((search) => search.collection)).toEqual([
      "job_posting",
      "job_posting",
      "job_posting",
    ]);
    expect(batch.searches[0]).toMatchObject({
      q: "engineer",
      sort_by: "_text_match:desc,first_seen_at:desc",
      per_page: 2,
      page: 1,
    });
    expect(batch.searches[1]).toMatchObject({ q: "engineer", per_page: 0 });
    expect(batch.searches[2]).toMatchObject({ q: "engineer", per_page: 0 });
    expect(String(batch.searches[2].filter_by)).toContain("first_seen_at:>");
  });

  it("retries the whole SDK batch after a transient transport failure", async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    try {
      mocks.multiSearch
        .mockRejectedValueOnce(Object.assign(new Error("reset"), { code: "ECONNRESET" }))
        .mockResolvedValueOnce(batchResponse());

      const pending = new TypesenseSearchProvider().loadPostingsWithCounts(params);
      await vi.runAllTimersAsync();

      await expect(pending).resolves.toMatchObject({ activeCount: 7, yearCount: 11 });
      expect(mocks.multiSearch).toHaveBeenCalledTimes(2);
      expect(warn).toHaveBeenCalledOnce();
    } finally {
      warn.mockRestore();
      vi.useRealTimers();
    }
  });

  it("fails the entire SDK batch when a result slot is missing", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mocks.multiSearch.mockResolvedValue({ results: batchResponse().results.slice(0, 2) });

    await expect(
      new TypesenseSearchProvider().loadPostingsWithCounts(params),
    ).resolves.toEqual({ postings: [], activeCount: 0, yearCount: 0 });
    expect(mocks.multiSearch).toHaveBeenCalledOnce();
    error.mockRestore();
  });

  it.each(malformedPostingSlots)(
    "fails the entire SDK batch for %s",
    async (_label, postingSlot) => {
      const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
      try {
        mocks.multiSearch.mockResolvedValue({
          results: [postingSlot, { found: 7 }, { found: 11 }],
        });

        await expect(
          new TypesenseSearchProvider().loadPostingsWithCounts(params),
        ).resolves.toEqual({ postings: [], activeCount: 0, yearCount: 0 });
        expect(mocks.multiSearch).toHaveBeenCalledOnce();
      } finally {
        error.mockRestore();
      }
    },
  );

  it("uses one browser multi_search and preserves ordered result mapping", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/typesense-key") {
        return Response.json({
          apiKey: "browser-key",
          host: "typesense.example",
          port: 443,
          protocol: "https",
          expiresAt: Date.now() + 60_000,
        });
      }
      expect(url).toBe("https://typesense.example:443/multi_search");
      expect(init?.method).toBe("POST");
      return Response.json(batchResponse());
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await new TypesenseBrowserProvider().loadPostingsWithCounts(params);

    expect(result).toMatchObject({ activeCount: 7, yearCount: 11 });
    expect(result.postings.map((posting) => posting.id)).toEqual([
      freshPosting.document.id,
      freshRole.document.id,
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const batchCall = fetchMock.mock.calls[1];
    const body = JSON.parse(String(batchCall[1]?.body)) as {
      searches: Array<Record<string, unknown>>;
    };
    expect(body.searches).toHaveLength(3);
    expect(body.searches[0]).toMatchObject({ per_page: 2, page: 1 });
    expect(body.searches[1]).toMatchObject({ per_page: 0 });
    expect(body.searches[2]).toMatchObject({ per_page: 0 });
  });

  it("rejects a browser batch that contains a per-search error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        if (String(input) === "/api/typesense-key") {
          return Response.json({
            apiKey: "browser-key",
            host: "typesense.example",
            port: 443,
            protocol: "https",
            expiresAt: Date.now() + 60_000,
          });
        }
        const response = batchResponse();
        response.results[1] = { found: 0, error: "bad filter" } as never;
        return Response.json(response);
      }),
    );

    await expect(
      new TypesenseBrowserProvider().loadPostingsWithCounts(params),
    ).rejects.toThrow("Typesense multi_search response was malformed");
  });

  it.each(malformedPostingSlots)(
    "rejects a browser batch with %s",
    async (_label, postingSlot) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: string | URL | Request) => {
          if (String(input) === "/api/typesense-key") {
            return Response.json({
              apiKey: "browser-key",
              host: "typesense.example",
              port: 443,
              protocol: "https",
              expiresAt: Date.now() + 60_000,
            });
          }
          return Response.json({
            results: [postingSlot, { found: 7 }, { found: 11 }],
          });
        }),
      );

      await expect(
        new TypesenseBrowserProvider().loadPostingsWithCounts(params),
      ).rejects.toThrow("Typesense multi_search response was malformed");
    },
  );
});

describe("resolveTypesenseCompany", () => {
  it("falls back to a later valid posting when the canonical document is unavailable", () => {
    const blank = postingHit("company-a", "", NOW).document;
    blank.company_slug = "";
    const valid = postingHit("company-a", "Advance Auto Parts", NOW - DAY).document;

    expect(resolveTypesenseCompany("company-a", [blank, valid])).toEqual({
      id: "company-a",
      name: "Advance Auto Parts",
      slug: "company-a",
      icon: null,
    });
  });

  it("rejects a group that has no usable company identity", () => {
    const blank = postingHit("company-a", "", NOW).document;
    blank.company_slug = "";

    expect(resolveTypesenseCompany("company-a", [blank])).toBeNull();
  });
});
