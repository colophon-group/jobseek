import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { setTestEnv, withTestEnv } from "@/test-utils/env";
import {
  SEARCH_BAR_TYPEAHEAD_MAX_DATA_REQUESTS,
  SEARCH_BAR_TYPEAHEAD_MAX_MULTI_SEARCH_REQUESTS,
  type SearchBarTypeaheadResults,
} from "../typeahead-contract";
import { clearTypesenseBrowserConfig } from "../typesense-browser-key";
import { suggestSearchBarBrowser } from "../typesense-browser-typeahead";

const mocks = vi.hoisted(() => ({ serverBatch: vi.fn() }));

vi.mock("@/lib/actions/locations", () => ({ suggestLocations: vi.fn() }));
vi.mock("@/lib/actions/taxonomy", () => ({
  suggestOccupations: vi.fn(),
  suggestSeniorities: vi.fn(),
  suggestTechnologies: vi.fn(),
}));
vi.mock("@/lib/actions/typeahead", () => ({
  suggestSearchBarTypeahead: mocks.serverBatch,
}));

type DataRequestKind = "key" | "candidate" | "fallback" | "boost";

const emptyResults: SearchBarTypeaheadResults = {
  locations: [],
  companies: [],
  occupations: [],
  seniorities: [],
  technologies: [],
};

function makeDataFetch(options: { failFallback?: boolean } = {}) {
  const calls: DataRequestKind[] = [];
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input) === "/api/typesense-key") {
      calls.push("key");
      return new Response(
        JSON.stringify({
          apiKey: "test-key",
          host: "typesense.test",
          port: 443,
          protocol: "https",
          expiresAt: Date.now() + 600_000,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }

    const searches = (JSON.parse(String(init?.body)) as {
      searches: Array<Record<string, unknown>>;
    }).searches;
    const isBoost = searches.every((search) => search.collection === "job_posting");
    const isFallback = searches.every(
      (search) =>
        (search.collection === "occupation" || search.collection === "seniority") &&
        search.filter_by === "has_active_postings:true && locale:en",
    );
    const kind: DataRequestKind = isBoost ? "boost" : isFallback ? "fallback" : "candidate";
    calls.push(kind);
    if (kind === "fallback" && options.failFallback) {
      return new Response("fallback failed", { status: 503 });
    }

    const results = searches.map((search) => {
      if (search.collection === "location") {
        return {
          hits: [
            {
              document: {
                location_id: 100,
                slug: "berlin",
                type: "city",
                name_en: "Berlin",
                parent_name: "Germany",
              },
            },
          ],
        };
      }
      if (search.collection === "company") {
        return {
          hits: [{ document: { id: "acme", name: "Acme", slug: "acme", icon: null } }],
        };
      }
      if (search.collection === "technology") {
        return {
          hits: [{ document: { technology_id: 40, slug: "react", name: "React" } }],
        };
      }
      if (search.collection === "occupation") {
        return search.filter_by === "has_active_postings:true && locale:en"
          ? {
              hits: [
                { document: { occupation_id: 20, slug: "engineer", name: "Engineer" } },
              ],
            }
          : { hits: [] };
      }
      if (search.collection === "seniority") {
        return search.filter_by === "has_active_postings:true && locale:en"
          ? {
              hits: [{ document: { seniority_id: 30, slug: "senior", name: "Senior" } }],
            }
          : { hits: [] };
      }
      return {
        facet_counts: [
          {
            field_name: search.facet_by,
            counts: [{ value: "100", count: 1 }],
          },
        ],
      };
    });
    return new Response(JSON.stringify({ results }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  return { calls, fetchMock };
}

describe("search-bar application-data request budget", () => {
  const originalFetch = globalThis.fetch;
  withTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });

  beforeEach(() => {
    vi.clearAllMocks();
    clearTypesenseBrowserConfig();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("enforces cold-key, warm-key, and warm-candidate direct budgets", async () => {
    const { calls, fetchMock } = makeDataFetch();
    globalThis.fetch = fetchMock;
    const filters = { keywords: ["engineer"] };
    const coldParams = {
      query: "budget-cold-de-6639",
      locale: "de",
      includeCompanies: true,
      locationFilters: filters,
      occupationFilters: filters,
      seniorityFilters: filters,
      technologyFilters: filters,
    };

    await suggestSearchBarBrowser(coldParams);
    expect(calls).toEqual(["key", "candidate", "fallback", "boost"]);
    expect(calls).toHaveLength(SEARCH_BAR_TYPEAHEAD_MAX_DATA_REQUESTS);

    const beforeWarmCandidates = calls.length;
    await suggestSearchBarBrowser(coldParams);
    expect(calls.slice(beforeWarmCandidates)).toEqual(["boost"]);

    const beforeWarmKey = calls.length;
    await suggestSearchBarBrowser({
      ...coldParams,
      query: "budget-warm-key-de-6639",
    });
    expect(calls.slice(beforeWarmKey)).toEqual(["candidate", "fallback", "boost"]);
    expect(calls.length - beforeWarmKey).toBe(
      SEARCH_BAR_TYPEAHEAD_MAX_MULTI_SEARCH_REQUESTS,
    );
  });

  it("uses one application data request with direct mode disabled", async () => {
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "0" });
    vi.resetModules();
    mocks.serverBatch.mockResolvedValue(emptyResults);
    globalThis.fetch = vi.fn(() => {
      throw new Error("direct fetch must not run");
    });
    const { runSearchBarTypeahead } = await import("../typeahead-runner");

    await expect(
      runSearchBarTypeahead({ query: "budget-direct-off-6639", locale: "en", includeCompanies: true }),
    ).resolves.toBe(emptyResults);
    expect(mocks.serverBatch).toHaveBeenCalledOnce();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("stops a failed fallback phase before adding one server-action request", async () => {
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });
    vi.resetModules();
    const { calls, fetchMock } = makeDataFetch({ failFallback: true });
    globalThis.fetch = fetchMock;
    mocks.serverBatch.mockResolvedValue(emptyResults);
    const { runSearchBarTypeahead } = await import("../typeahead-runner");

    await expect(
      runSearchBarTypeahead({
        query: "budget-fallback-failure-de-6639",
        locale: "de",
        includeCompanies: true,
      }),
    ).resolves.toBe(emptyResults);

    expect(calls).toEqual(["key", "candidate", "fallback"]);
    expect(mocks.serverBatch).toHaveBeenCalledOnce();
    expect(calls.length + mocks.serverBatch.mock.calls.length).toBe(
      SEARCH_BAR_TYPEAHEAD_MAX_DATA_REQUESTS,
    );
  });

  it("documents the data-request scope and wire-level exclusions", () => {
    const docs = readFileSync(resolve(process.cwd(), "../../docs/11-typesense.md"), "utf8");
    expect(docs).toContain("application-initiated data requests");
    expect(docs).toContain("dynamic-import chunks");
    expect(docs).toContain("`OPTIONS` preflights");
  });
});
