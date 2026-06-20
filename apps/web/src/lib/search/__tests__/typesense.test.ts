import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type SearchCall = {
  collection: string;
  params: Record<string, unknown>;
};

const mocks = vi.hoisted(() => ({
  calls: [] as SearchCall[],
  browserCalls: [] as SearchCall[],
  search: vi.fn(),
}));

vi.mock("../typesense-client", () => ({
  getSearchClient: () => ({
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
import { POSTING_BASE_FILTER } from "../typesense-filters";
import { TypesenseSearchProvider } from "../typesense";

const NOW = Math.floor(Date.UTC(2026, 5, 19) / 1000);
const DAY = 86_400;

function postingHit(
  companyId: string,
  companyName: string,
  firstSeenAt: number,
) {
  return {
    document: {
      id: `${companyId}-posting`,
      company_id: companyId,
      company_name: companyName,
      company_slug: companyId,
      title: `${companyName} role`,
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
    // Deliberately return company docs in active-count order. Providers must
    // preserve the freshness order from grouped postings.
    hits: [
      companyHit("stale-bigco", "Stale BigCo", 50_000, 50_000),
      companyHit("fresh-co", "Fresh Co", 1, 1),
    ],
  };
}

beforeEach(() => {
  mocks.calls.length = 0;
  mocks.browserCalls.length = 0;
  mocks.search.mockReset();
  clearTypesenseBrowserConfig();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TypesenseSearchProvider.listTopCompanies", () => {
  it("ranks the anonymous default surface by freshest active posting, not company size", async () => {
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
      "fresh-co",
      "stale-bigco",
    ]);
    expect(result.companies.map((c) => c.activeMatches)).toEqual([1, 50_000]);
    expect(result.totalCompanies).toBe(2);
    expect(mocks.calls[0]).toMatchObject({
      collection: "job_posting",
      params: {
        q: "*",
        filter_by: POSTING_BASE_FILTER,
        group_by: "company_id",
        group_limit: 10,
        sort_by: "first_seen_at:desc",
        per_page: 2,
        page: 1,
      },
    });
    expect(mocks.calls[1]).toMatchObject({
      collection: "company",
      params: {
        q: "*",
        filter_by: "id:[fresh-co,stale-bigco]",
        per_page: 2,
      },
    });
  });
});

describe("TypesenseBrowserProvider.listTopCompanies", () => {
  it("uses the same freshness ranking as the server provider", async () => {
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
      "fresh-co",
      "stale-bigco",
    ]);
    expect(result.totalCompanies).toBe(2);
    expect(mocks.browserCalls[0]).toMatchObject({
      collection: "job_posting",
      params: {
        q: "*",
        filter_by: POSTING_BASE_FILTER,
        group_by: "company_id",
        group_limit: "10",
        sort_by: "first_seen_at:desc",
        per_page: "2",
        page: "1",
      },
    });
    expect(mocks.browserCalls[1]).toMatchObject({
      collection: "company",
      params: {
        q: "*",
        filter_by: "id:[fresh-co,stale-bigco]",
        per_page: "2",
      },
    });
  });
});
