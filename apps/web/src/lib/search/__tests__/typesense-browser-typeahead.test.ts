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

import { suggestLocationsBrowser } from "../typesense-browser-typeahead";

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
