import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getTypesenseBrowserConfig: vi.fn(async () => ({
    apiKey: "test-key",
    host: "typesense.test",
    port: 443,
    protocol: "https",
    expiresAt: Date.now() + 60_000,
  })),
}));

vi.mock("../typesense-browser-key", () => ({
  getTypesenseBrowserConfig: mocks.getTypesenseBrowserConfig,
}));

import {
  getWatchlistPostingsBrowser,
  getWatchlistPostingYearCountBrowser,
} from "../typesense-browser-watchlist";

function makeUuid(index: number): string {
  return `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`;
}

function validDocument(index = 1) {
  return {
    id: `posting-${index}`,
    first_seen_at: 1_700_000_000 + index,
    company_id: `company-${index}`,
    company_name: "Acme",
    company_slug: "acme",
  };
}

describe("getWatchlistPostingsBrowser (#3477)", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it("falls back before sending an oversized company-id filter", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    globalThis.fetch = fetchMock;

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: Array.from({ length: 99 }, (_, i) => makeUuid(i + 1)),
        offset: 0,
        limit: 20,
      }),
    ).rejects.toThrow("watchlist Typesense query exceeds GET limit");

    expect(mocks.getTypesenseBrowserConfig).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the flow filter for a browser-direct year count", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({ found: 31 }),
    } as Response);
    globalThis.fetch = fetchMock;

    await expect(
      getWatchlistPostingYearCountBrowser({
        companyIds: [makeUuid(1)],
        languages: ["en"],
      }),
    ).resolves.toBe(31);

    const requestUrl = new URL(String(fetchMock.mock.calls[0]?.[0]));
    const filter = requestUrl.searchParams.get("filter_by") ?? "";
    expect(filter).toContain("has_content:!=false");
    expect(filter).toContain("first_seen_at:>");
    expect(filter).toContain(`company_id:[${makeUuid(1)}]`);
    expect(filter).not.toContain("is_active:true");
    expect(requestUrl.searchParams.get("per_page")).toBe("0");
  });

  it("preserves posting locations in a browser refresh", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({
        found: 1,
        hits: [{
          document: {
            id: "posting-1",
            first_seen_at: 1_700_000_000,
            company_id: "company-1",
            company_name: "Acme",
            company_slug: "acme",
            location_names: ["Zurich", "Switzerland"],
          },
        }],
      }),
    } as Response);
    globalThis.fetch = fetchMock;

    const result = await getWatchlistPostingsBrowser({
      companyIds: [makeUuid(1)],
      offset: 0,
      limit: 20,
    });

    expect(result.postings[0]?.locationNames).toEqual([
      "Zurich",
      "Switzerland",
    ]);
  });

  it.each([
    ["a missing found count", { hits: [] }],
    ["a fractional found count", { found: 1.5, hits: [] }],
    ["missing hits for a non-empty page", { found: 1 }],
    ["a non-object hit", { found: 1, hits: [null] }],
  ])("rejects HTTP-200 search payloads with %s", async (_label, payload) => {
    globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => payload,
    } as Response);

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: [makeUuid(1)],
        offset: 0,
        limit: 20,
      }),
    ).rejects.toThrow("Typesense response was malformed");
  });

  it.each([
    ["found>0 with an empty page", { found: 1, hits: [] }],
    ["found=0 with a hit", {
      found: 0,
      hits: [{ document: validDocument() }],
    }],
    ["more hits than the requested anonymous page", {
      found: 21,
      hits: Array.from({ length: 21 }, (_, index) => ({
        document: validDocument(index + 1),
      })),
    }],
  ])("rejects HTTP-200 search payloads with %s", async (_label, payload) => {
    globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => payload,
    } as Response);

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: [makeUuid(1)],
        offset: 0,
        limit: 20,
      }),
    ).rejects.toThrow("Typesense response was malformed");
  });

  it.each([
    ["id", { first_seen_at: 1_700_000_000 }],
    ["first_seen_at", { id: "posting-1" }],
    ["company field type", {
      id: "posting-1",
      first_seen_at: 1_700_000_000,
      company_name: 42,
    }],
  ])("rejects HTTP-200 hits with an invalid %s", async (_label, document) => {
    globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({ found: 1, hits: [{ document }] }),
    } as Response);

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: [makeUuid(1)],
        offset: 0,
        limit: 20,
      }),
    ).rejects.toThrow("Typesense response was malformed");
  });

  it("rejects an HTTP-200 year count without a valid found field", async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({}),
    } as Response);

    await expect(
      getWatchlistPostingYearCountBrowser({
        companyIds: [makeUuid(1)],
      }),
    ).rejects.toThrow("Typesense response was malformed");
  });

  it("normalizes mixed or non-array location fields like the server mapper", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          found: 1,
          hits: [{
            document: {
              ...validDocument(),
              location_names: [42, "", "Zurich", null],
            },
          }],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          found: 1,
          hits: [{
            document: {
              ...validDocument(2),
              location_names: "Zurich",
            },
          }],
        }),
      } as Response);
    globalThis.fetch = fetchMock;

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: [makeUuid(1)],
        offset: 0,
        limit: 20,
      }),
    ).resolves.toMatchObject({ postings: [{ locationNames: ["Zurich"] }] });
    await expect(
      getWatchlistPostingsBrowser({
        companyIds: [makeUuid(1)],
        offset: 0,
        limit: 20,
      }),
    ).resolves.toMatchObject({ postings: [{ locationNames: [] }] });
  });
});
