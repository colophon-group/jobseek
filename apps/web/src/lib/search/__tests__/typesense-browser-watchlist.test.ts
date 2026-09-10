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

  it("splits an oversized company-id filter into URL-safe browser batches", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({ found: 0, hits: [] }),
    } as Response);
    globalThis.fetch = fetchMock;

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: Array.from({ length: 99 }, (_, i) => makeUuid(i + 1)),
        offset: 0,
        limit: 20,
      }),
    ).resolves.toEqual({ postings: [], total: 0 });

    expect(mocks.getTypesenseBrowserConfig).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    for (const [request] of fetchMock.mock.calls) {
      expect(String(request).length).toBeLessThanOrEqual(7_500);
    }
  });

  it("sums year counts across large persisted watchlists", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({ found: 7 }),
    } as Response);
    globalThis.fetch = fetchMock;

    const count = await getWatchlistPostingYearCountBrowser({
      companyIds: Array.from({ length: 101 }, (_, i) => makeUuid(i + 1)),
    });

    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    expect(count).toBe(fetchMock.mock.calls.length * 7);
  });

  it("paginates each company batch past Typesense's 250-row page limit", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (request) => {
      const url = new URL(String(request));
      const offset = Number(url.searchParams.get("offset") ?? "0");
      const limit = Number(url.searchParams.get("limit") ?? "0");
      const hitCount = Math.min(limit, Math.max(0, 260 - offset));
      return {
        ok: true,
        json: async () => ({
          found: 260,
          hits: Array.from({ length: hitCount }, (_, index) => ({
            document: validDocument(offset + index + 1),
          })),
        }),
      } as Response;
    });
    globalThis.fetch = fetchMock;

    const result = await getWatchlistPostingsBrowser({
      companyIds: Array.from({ length: 101 }, (_, i) => makeUuid(i + 1)),
      offset: 240,
      limit: 20,
    });

    expect(result.postings).toHaveLength(20);
    expect(result.total).toBeGreaterThanOrEqual(520);
    const urls = fetchMock.mock.calls.map(([request]) => new URL(String(request)));
    expect(urls.some((url) => (
      url.searchParams.get("offset") === "250"
      && url.searchParams.get("limit") === "10"
    ))).toBe(true);
    expect(urls.every((url) => (
      Number(url.searchParams.get("limit")) <= 250
      && url.searchParams.get("per_page") === null
    ))).toBe(true);
  });

  it.each([108, 250])(
    "keeps repeated load-more windows exact and bounded for %i companies",
    async (companyCount) => {
      const companyIds = Array.from(
        { length: companyCount },
        (_, index) => makeUuid(index + 1),
      );
      const fetchMock = vi.fn<typeof fetch>().mockImplementation(
        async (request) => {
          const url = new URL(String(request));
          const filter = url.searchParams.get("filter_by") ?? "";
          const batchCompanyIds = filter.match(
            /00000000-0000-0000-0000-\d{12}/g,
          ) ?? [];
          const offset = Number(url.searchParams.get("offset") ?? "0");
          const limit = Number(url.searchParams.get("limit") ?? "0");
          const documents = batchCompanyIds
            .map((companyId) => Number(companyId.slice(-12)))
            .sort((a, b) => b - a)
            .map((index) => ({
              ...validDocument(index),
              id: `posting-${index}`,
              first_seen_at: 1_700_000_000 + index,
            }));

          return {
            ok: true,
            json: async () => ({
              found: documents.length,
              hits: documents.slice(offset, offset + limit).map((document) => ({
                document,
              })),
            }),
          } as Response;
        },
      );
      globalThis.fetch = fetchMock;

      const expectedOrder = Array.from(
        { length: companyCount },
        (_, index) => `posting-${companyCount - index}`,
      );

      for (const offset of [0, 20, 40]) {
        const firstCall = fetchMock.mock.calls.length;
        const result = await getWatchlistPostingsBrowser({
          companyIds,
          offset,
          limit: 20,
        });

        expect(result.total).toBe(companyCount);
        expect(result.postings.map((posting) => posting.id)).toEqual(
          expectedOrder.slice(offset, offset + 20),
        );

        const windowUrls = fetchMock.mock.calls
          .slice(firstCall)
          .map(([request]) => new URL(String(request)));
        expect(windowUrls.length).toBeGreaterThan(1);
        expect(windowUrls.every((url) => (
          url.searchParams.get("offset") === "0"
          && url.searchParams.get("limit") === String(offset + 20)
          && url.searchParams.get("per_page") === null
        ))).toBe(true);
      }
    },
  );

  it("keeps tied batched result prefixes stable across browser page boundaries", async () => {
    const companyIds = Array.from(
      { length: 101 },
      (_, index) => makeUuid(index + 1),
    );
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(
      async (request) => {
        const url = new URL(String(request));
        const filter = url.searchParams.get("filter_by") ?? "";
        const isFirstBatch = filter.includes(makeUuid(1));
        const offset = Number(url.searchParams.get("offset") ?? "0");
        const limit = Number(url.searchParams.get("limit") ?? "0");
        const documents = Array.from({ length: 40 }, (_, index) => ({
          ...validDocument(),
          // Later hits from every other batch deliberately sort before its
          // earlier hits by ID. Re-sorting growing prefixes by ID would make
          // page 2 repeat page 1 and omit results.
          id: isFirstBatch
            ? `posting-z-${String(index).padStart(2, "0")}`
            : `${index < 20 ? "posting-m" : "posting-a"}-${String(index).padStart(2, "0")}`,
          first_seen_at: 1_700_000_000,
        }));
        return {
          ok: true,
          json: async () => ({
            found: documents.length,
            hits: documents.slice(offset, offset + limit).map((document) => ({
              text_match: 100,
              document,
            })),
          }),
        } as Response;
      },
    );
    globalThis.fetch = fetchMock;

    const firstPage = await getWatchlistPostingsBrowser({
      companyIds,
      keywords: ["engineer"],
      offset: 0,
      limit: 20,
    });
    const secondPage = await getWatchlistPostingsBrowser({
      companyIds,
      keywords: ["engineer"],
      offset: 20,
      limit: 20,
    });

    expect(firstPage.postings.map((posting) => posting.id)).toEqual(
      Array.from(
        { length: 20 },
        (_, index) => `posting-z-${String(index).padStart(2, "0")}`,
      ),
    );
    expect(secondPage.postings.map((posting) => posting.id)).toEqual(
      Array.from(
        { length: 20 },
        (_, index) => `posting-z-${String(index + 20).padStart(2, "0")}`,
      ),
    );
    expect(new Set([
      ...firstPage.postings.map((posting) => posting.id),
      ...secondPage.postings.map((posting) => posting.id),
    ])).toHaveLength(40);
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
