/**
 * Unit tests for the browser-side Typesense typeahead.
 *
 * Focused on the macro-region alias behaviour from issue #2939: searching
 * for a natural-language synonym ("Europe", "European Union") must surface
 * the EU macro row whose canonical ``name_en`` is just the abbreviation.
 *
 * Strategy: mock ``getTypesenseBrowserConfig`` so the module skips the
 * scoped-key endpoint, and stub ``fetch`` to capture the outgoing request
 * (asserting the ``query_by`` field list) and inject a canned response.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../typesense-browser-key", () => ({
  getTypesenseBrowserConfig: vi.fn(async () => ({
    apiKey: "test-key",
    host: "typesense.test",
    port: 443,
    protocol: "https",
    expiresAt: Date.now() + 60_000,
  })),
}));

import {
  suggestLocationsBrowser,
  suggestSearchBarBrowser,
} from "../typesense-browser-typeahead";

const EU_DOC = {
  location_id: 4,
  slug: "eu",
  type: "macro",
  name_en: "EU",
  aliases: ["European Union", "Europe", "EEA", "Schengen"],
};

const BERLIN_DOC = {
  location_id: 100,
  slug: "berlin",
  type: "city",
  name_en: "Berlin",
  parent_name: "Germany",
};

interface CapturedCall {
  url: string;
  params: URLSearchParams;
}

function makeFetchStub(
  documents: Array<{ doc: typeof EU_DOC | typeof BERLIN_DOC; aliasMatch?: string }>,
): { fetchMock: typeof globalThis.fetch; calls: CapturedCall[] } {
  const calls: CapturedCall[] = [];
  const fetchMock: typeof globalThis.fetch = async (input) => {
    const url = String(input);
    const queryStart = url.indexOf("?");
    const params = new URLSearchParams(queryStart >= 0 ? url.slice(queryStart + 1) : "");
    calls.push({ url, params });
    const body = JSON.stringify({
      hits: documents.map(({ doc, aliasMatch }) => ({
        document: doc,
        highlights: aliasMatch
          ? [{ field: "aliases", snippets: [`<mark>${aliasMatch}</mark>`] }]
          : [],
      })),
    });
    return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
  };
  return { fetchMock, calls };
}

describe("suggestLocationsBrowser — macro region aliases (#2939)", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    // Each test starts from a clean module-internal LRU cache. Because
    // the typeahead module caches by query+locale+geo, isolated unique
    // queries per test keep cache hits from leaking across tests.
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("includes ``aliases`` in the query_by field list (en locale)", async () => {
    const { fetchMock, calls } = makeFetchStub([
      { doc: EU_DOC, aliasMatch: "Europe" },
    ]);
    globalThis.fetch = fetchMock;

    const out = await suggestLocationsBrowser({
      query: "Europe-en-test1",
      locale: "en",
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].params.get("query_by")).toBe("name_en,aliases");
    expect(calls[0].params.get("query_by_weights")).toBe("2,1");
    // EU surfaced via the alias match, mapped to the canonical name.
    expect(out.map((s) => s.slug)).toEqual(["eu"]);
    expect(out[0].name).toBe("EU");
    expect(out[0].type).toBe("macro");
  });

  it("includes ``aliases`` for a non-English locale", async () => {
    const { fetchMock, calls } = makeFetchStub([
      { doc: EU_DOC, aliasMatch: "Europe" },
    ]);
    globalThis.fetch = fetchMock;

    await suggestLocationsBrowser({
      query: "Europe-de-test2",
      locale: "de",
    });

    expect(calls[0].params.get("query_by")).toBe("name_de,name_en,aliases");
    expect(calls[0].params.get("query_by_weights")).toBe("3,2,1");
  });

  it("returns the EU macro when the user types ``Europe`` (alias-only match)", async () => {
    const { fetchMock } = makeFetchStub([
      { doc: EU_DOC, aliasMatch: "Europe" },
    ]);
    globalThis.fetch = fetchMock;

    const out = await suggestLocationsBrowser({
      query: "Europe-test3",
      locale: "en",
    });

    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({
      slug: "eu",
      type: "macro",
      // Display name stays the canonical ``EU`` — the dropdown only
      // shows the alias as a hint via highlights, never as the label.
      name: "EU",
    });
  });

  it("still returns canonical-name matches like ``Berlin`` alongside aliases", async () => {
    const { fetchMock } = makeFetchStub([{ doc: BERLIN_DOC }]);
    globalThis.fetch = fetchMock;

    const out = await suggestLocationsBrowser({
      query: "Berlin-test4",
      locale: "en",
    });

    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({
      slug: "berlin",
      type: "city",
      name: "Berlin",
      parentName: "Germany",
    });
  });
});

describe("suggestSearchBarBrowser — bounded multi_search plan (#6639)", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("batches all uncached candidate collections into one request", async () => {
    const calls: Array<{ url: string; searches: Array<Record<string, unknown>> }> = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      const searches = (JSON.parse(String(init?.body)) as {
        searches: Array<Record<string, unknown>>;
      }).searches;
      calls.push({ url: String(input), searches });
      return new Response(
        JSON.stringify({ results: searches.map(() => ({ hits: [] })) }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });

    const params = {
      query: "batch-candidates-6639",
      locale: "en",
      includeCompanies: true,
    };
    await suggestSearchBarBrowser(params);
    await suggestSearchBarBrowser(params);

    // The second call is served entirely from the existing per-kind LRUs.
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("https://typesense.test:443/multi_search");
    expect(calls[0].searches.map((search) => search.collection)).toEqual([
      "location",
      "company",
      "occupation",
      "seniority",
      "technology",
    ]);
  });

  it("uses at most three multi_search phases for fallback and posting boosts", async () => {
    const calls: Array<Array<Record<string, unknown>>> = [];
    globalThis.fetch = vi.fn(async (_input, init) => {
      const searches = (JSON.parse(String(init?.body)) as {
        searches: Array<Record<string, unknown>>;
      }).searches;
      calls.push(searches);
      const results = searches.map((search) => {
        if (search.collection === "location") {
          return { hits: [{ document: BERLIN_DOC }] };
        }
        if (search.collection === "company") {
          return {
            hits: [{ document: { id: "acme", name: "Acme", slug: "acme", icon: null } }],
          };
        }
        if (search.collection === "technology") {
          return {
            hits: [
              { document: { technology_id: 40, slug: "react", name: "React" } },
              { document: { technology_id: 41, slug: "rust", name: "Rust" } },
            ],
          };
        }
        if (
          search.collection === "occupation" &&
          search.filter_by === "has_active_postings:true && locale:en"
        ) {
          return {
            hits: [{ document: { occupation_id: 20, slug: "engineer", name: "Engineer" } }],
          };
        }
        if (
          search.collection === "seniority" &&
          search.filter_by === "has_active_postings:true && locale:en"
        ) {
          return {
            hits: [{ document: { seniority_id: 30, slug: "senior", name: "Senior" } }],
          };
        }
        if (search.collection === "job_posting") {
          const matchedId =
            search.facet_by === "location_ids"
              ? "100"
              : search.facet_by === "occupation_id"
                ? "20"
                : search.facet_by === "seniority_id"
                  ? "30"
                  : "41";
          return {
            facet_counts: [
              {
                field_name: search.facet_by,
                counts: [{ value: matchedId, count: 1 }],
              },
            ],
          };
        }
        return { hits: [] };
      });
      return new Response(JSON.stringify({ results }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });

    const filters = { keywords: ["engineer"] };
    const result = await suggestSearchBarBrowser({
      query: "cold-de-plan-6639",
      locale: "de",
      includeCompanies: true,
      locationFilters: filters,
      occupationFilters: filters,
      seniorityFilters: filters,
      technologyFilters: filters,
    });

    expect(calls).toHaveLength(3);
    expect(calls[0].map((search) => search.collection)).toEqual([
      "location",
      "company",
      "occupation",
      "seniority",
      "technology",
    ]);
    expect(calls[1].map((search) => search.collection)).toEqual([
      "occupation",
      "seniority",
    ]);
    expect(calls[2]).toHaveLength(4);
    expect(calls[2].every((search) => search.collection === "job_posting")).toBe(true);
    expect(result.companies[0].name).toBe("Acme");
    expect(result.occupations[0].name).toBe("Engineer");
    expect(result.seniorities[0].name).toBe("Senior");
    expect(result.technologies.map((technology) => technology.name)).toEqual([
      "Rust",
      "React",
    ]);
  });
});
