import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getCurrencyRates: vi.fn(),
  resolveLocationSlugs: vi.fn(),
  resolveOccupationSlugs: vi.fn(),
  resolveSenioritySlugs: vi.fn(),
  resolveTechnologySlugs: vi.fn(),
  multiSearch: vi.fn(),
  singleSearch: vi.fn(),
}));

vi.mock("@/lib/services/search", () => ({
  getCurrencyRates: mocks.getCurrencyRates,
}));
vi.mock("@/lib/services/locations", () => ({
  resolveLocationSlugs: mocks.resolveLocationSlugs,
}));
vi.mock("@/lib/services/taxonomy", () => ({
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.singleSearch }) }),
    multiSearch: { perform: mocks.multiSearch },
  }),
}));
vi.mock("@/lib/search/typesense-retry", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/search/typesense-retry")
  >();
  return {
    ...actual,
    withTypesenseRetry: (operation: () => Promise<unknown>) => operation(),
  };
});
import {
  compileWatchlistMatcherSources,
  matchCompiledWatchlistsInWindow,
  readWatchlistCandidates,
} from "../watchlist-matcher";
import { candidateOrderKeyFromCanonicalId } from "@/lib/search/watchlist-candidate-query";

const READY_RECEIPT = Buffer.from(JSON.stringify({
  authoritativeCount: 10_000,
  benchmarkSha256: "a".repeat(64),
  completedAt: "2026-09-11T10:00:00Z",
  keyVersion: "uuid-b64lex-v1",
  partitions: 256,
  reconciliationRunId: "00000000-0000-0000-0000-000000000001",
  schemaVersion: "typesense-stable-candidate-order-readiness-v1",
  unresolved: 0,
})).toString("base64url");

withTestEnv({ TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: READY_RECEIPT });

function posting(id: string, firstSeenAt: number) {
  const isCanonicalUuid =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id);
  return {
    document: {
      id,
      candidate_order_key: isCanonicalUuid
        ? candidateOrderKeyFromCanonicalId(id)
        : undefined,
      title: `Role ${id}`,
      source_url: `https://example.test/${id}`,
      first_seen_at: firstSeenAt,
      is_active: true,
      company_id: "company-1",
      company_name: "Acme",
      company_slug: "acme",
      location_names: ["Zurich"],
    },
  };
}

function makeUuid(index: number): string {
  return `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getCurrencyRates.mockResolvedValue([
    { currency: "USD", toEur: 0.9 },
  ]);
  mocks.resolveLocationSlugs.mockResolvedValue(
    new Map([
      [
        "zurich",
        {
          id: 10,
          slug: "zurich",
          name: "Zürich",
          type: "city",
          parentName: "Schweiz",
        },
      ],
    ]),
  );
  mocks.resolveOccupationSlugs.mockResolvedValue(
    new Map([["engineering", { id: 20, slug: "engineering", name: "Engineering" }]]),
  );
  mocks.resolveSenioritySlugs.mockResolvedValue(
    new Map([["senior", { id: 30, slug: "senior", name: "Senior" }]]),
  );
  mocks.resolveTechnologySlugs.mockResolvedValue(
    new Map([["typescript", { id: 40, slug: "typescript", name: "TypeScript" }]]),
  );
});

describe("compileWatchlistMatcherSources", () => {
  it("batch-resolves current taxonomy, currency, and owner locale semantics", async () => {
    const compiled = await compileWatchlistMatcherSources([
      {
        watchlistId: "watchlist-1",
        watchlistLabel: "Backend",
        filters: {
          keywords: ["staff"],
          locationSlugs: ["zurich"],
          occupationSlugs: ["engineering"],
          senioritySlugs: ["senior"],
          technologySlugs: ["typescript"],
          workMode: ["remote"],
          employmentType: ["full_time"],
          salaryMin: 100_000,
          salaryCurrency: "USD",
          experienceMin: 3,
        },
        companyIds: ["company-1"],
        locale: "de",
        jobLanguages: [],
      },
      {
        watchlistId: "watchlist-2",
        watchlistLabel: "All roles",
        filters: { anyCompany: true, locationSlugs: ["zurich"] },
        companyIds: ["company-2"],
        locale: "de",
        jobLanguages: ["*"],
      },
    ]);

    expect(mocks.resolveLocationSlugs).toHaveBeenCalledTimes(1);
    expect(mocks.resolveLocationSlugs).toHaveBeenCalledWith(["zurich"], "de");
    expect(mocks.getCurrencyRates).toHaveBeenCalledTimes(1);
    expect(compiled[0]?.candidateFilters).toMatchObject({
      companyIds: ["company-1"],
      keywords: ["staff"],
      locationIds: [10],
      occupationIds: [20],
      seniorityIds: [30],
      technologyIds: [40],
      workMode: ["remote"],
      employmentType: ["full_time"],
      salaryMin: 90_000,
      experienceMin: 3,
      languages: ["de"],
    });
    expect(compiled[1]?.candidateFilters).toMatchObject({
      companyIds: [],
      anyCompany: true,
      locationIds: [10],
      languages: [],
    });
  });
});

describe("readWatchlistCandidates", () => {
  it("fails closed before newest-first reads are marked backfill-ready", async () => {
    setTestEnv({ TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: undefined });

    await expect(readWatchlistCandidates({
      filters: { companyIds: [makeUuid(1)] },
      offset: 0,
      limit: 20,
      order: "newest",
      requireStableOrder: true,
    })).rejects.toThrow("has not passed backfill readiness");
    expect(mocks.singleSearch).not.toHaveBeenCalled();
  });

  it("orders equal-time batches by candidate ID across page boundaries", async () => {
    const companyIds = Array.from(
      { length: 101 },
      (_, index) => makeUuid(index + 1),
    );
    const lowIds = Array.from(
      { length: 40 },
      (_, index) => `10000000-0000-0000-0000-${String(index).padStart(12, "0")}`,
    );
    const highIds = Array.from(
      { length: 40 },
      (_, index) => `f0000000-0000-0000-0000-${String(index).padStart(12, "0")}`,
    );
    mocks.singleSearch.mockImplementation((search: {
      filter_by?: string;
      per_page?: number;
      offset?: number;
      limit?: number;
      sort_by?: string;
    }) => {
      if (search.per_page === 0) return { found: 40, hits: [] };
      const isFirstBatch = (search.filter_by ?? "").includes(makeUuid(1));
      const hits = (isFirstBatch ? highIds : lowIds).map((id) =>
        posting(id, 1_700_000_000),
      );
      if ((search.sort_by ?? "").includes("missing_values")) {
        return { found: hits.length, hits: hits.slice(0, 1) };
      }
      const offset = search.offset ?? 0;
      const limit = search.limit ?? 0;
      return { found: hits.length, hits: hits.slice(offset, offset + limit) };
    });

    const filters = { companyIds };
    const firstPage = await readWatchlistCandidates({
      filters,
      offset: 0,
      limit: 20,
      order: "newest",
      requireStableOrder: true,
    });
    const secondPage = await readWatchlistCandidates({
      filters,
      offset: 20,
      limit: 20,
      order: "newest",
      requireStableOrder: true,
    });

    expect(firstPage.postings.map((value) => value.id)).toEqual(lowIds.slice(0, 20));
    expect(secondPage.postings.map((value) => value.id)).toEqual(lowIds.slice(20, 40));
  });

  it("globally rejects a missing order key before a direct offset page", async () => {
    const hit = posting(makeUuid(1), 1_700_000_000);
    delete (hit.document as { candidate_order_key?: string }).candidate_order_key;
    mocks.singleSearch.mockResolvedValue({ found: 1, hits: [hit] });

    await expect(readWatchlistCandidates({
      filters: { companyIds: [makeUuid(1)] },
      offset: 200,
      limit: 1,
      order: "newest",
      requireStableOrder: true,
    })).rejects.toThrow("response was malformed");
    expect(mocks.singleSearch).toHaveBeenCalledTimes(1);
    expect(mocks.singleSearch.mock.calls[0]?.[0]).toMatchObject({
      page: 1,
      per_page: 1,
      sort_by: "candidate_order_key(missing_values: first):asc",
    });
  });

  it.each(["mismatched key", "non-canonical ID"])(
    "rejects a newest-first hit with %s",
    async (failure) => {
      const hit = posting(makeUuid(1), 1_700_000_000);
      const document = hit.document as {
        id: string;
        candidate_order_key: string;
      };
      if (failure === "mismatched key") {
        document.candidate_order_key = candidateOrderKeyFromCanonicalId(makeUuid(2));
      } else {
        document.id = "A0000000-0000-0000-0000-000000000001";
      }
      mocks.singleSearch.mockResolvedValue({ found: 1, hits: [hit] });

      await expect(readWatchlistCandidates({
        filters: { companyIds: [makeUuid(1)] },
        offset: 0,
        limit: 1,
        order: "newest",
        requireStableOrder: true,
      })).rejects.toThrow("response was malformed");
    },
  );

  it("keeps tied batched prefixes stable across page boundaries", async () => {
    const companyIds = Array.from(
      { length: 101 },
      (_, index) => makeUuid(index + 1),
    );
    mocks.singleSearch.mockImplementation((search: {
      filter_by?: string;
      per_page?: number;
      offset?: number;
      limit?: number;
    }) => {
      if (search.per_page === 0) return { found: 40, hits: [] };
      const filter = search.filter_by ?? "";
      const isFirstBatch = filter.includes(makeUuid(1));
      const offset = search.offset ?? 0;
      const limit = search.limit ?? 0;
      const hits = Array.from({ length: 40 }, (_, index) => ({
        ...posting(
          isFirstBatch
            ? `posting-z-${String(index).padStart(2, "0")}`
            : `${index < 20 ? "posting-m" : "posting-a"}-${String(index).padStart(2, "0")}`,
          1_700_000_000,
        ),
        text_match: 100,
      }));
      return {
        found: hits.length,
        hits: hits.slice(offset, offset + limit),
      };
    });

    const filters = {
      companyIds,
      keywords: ["engineer"],
    };
    const firstPage = await readWatchlistCandidates({
      filters,
      offset: 0,
      limit: 20,
    });
    const secondPage = await readWatchlistCandidates({
      filters,
      offset: 20,
      limit: 20,
    });

    expect(firstPage.postings.map((value) => value.id)).toEqual(
      Array.from(
        { length: 20 },
        (_, index) => `posting-z-${String(index).padStart(2, "0")}`,
      ),
    );
    expect(secondPage.postings.map((value) => value.id)).toEqual(
      Array.from(
        { length: 20 },
        (_, index) => `posting-z-${String(index + 20).padStart(2, "0")}`,
      ),
    );
    expect(new Set([
      ...firstPage.postings.map((value) => value.id),
      ...secondPage.postings.map((value) => value.id),
    ])).toHaveLength(40);
  });

  it("keeps URL-safety company batches stable as the server prefix grows", async () => {
    const companyIds = Array.from(
      { length: 250 },
      (_, index) => makeUuid(index + 1),
    );
    mocks.singleSearch.mockResolvedValue({ found: 0, hits: [] });
    const filters = { companyIds, keywords: ["abcdefghijklmn"] };

    await readWatchlistCandidates({
      filters,
      offset: 60,
      limit: 20,
    });
    const firstPartitions = mocks.singleSearch.mock.calls.map(([search]) =>
      (((search as { filter_by?: string }).filter_by ?? "").match(
        /00000000-0000-0000-0000-\d{12}/g,
      ) ?? []),
    );

    mocks.singleSearch.mockClear();
    await readWatchlistCandidates({
      filters,
      offset: 80,
      limit: 20,
    });
    const secondPartitions = mocks.singleSearch.mock.calls.map(([search]) =>
      (((search as { filter_by?: string }).filter_by ?? "").match(
        /00000000-0000-0000-0000-\d{12}/g,
      ) ?? []),
    );

    expect(firstPartitions.length).toBeGreaterThan(1);
    expect(secondPartitions).toEqual(firstPartitions);
  });

  it("does not switch server query mode on deeper pages", async () => {
    const companyIds = Array.from(
      { length: 83 },
      (_, index) => makeUuid(index + 1),
    );
    mocks.singleSearch.mockResolvedValue({ found: 0, hits: [] });
    const filters = { companyIds, keywords: ["x".repeat(93)] };

    await readWatchlistCandidates({ filters, offset: 160, limit: 20 });
    const shallowCallCount = mocks.singleSearch.mock.calls.length;
    mocks.singleSearch.mockClear();
    await readWatchlistCandidates({
      filters,
      offset: 180,
      limit: 20,
    });

    expect(shallowCallCount).toBeGreaterThan(1);
    expect(mocks.singleSearch.mock.calls).toHaveLength(shallowCallCount);
  });
});

describe("matchCompiledWatchlistsInWindow", () => {
  it("keeps legacy notifications unchanged even when the receipt exists", async () => {
    mocks.multiSearch.mockResolvedValue({
      results: [{ found: 1, hits: [posting("legacy-hit", 1_700_000_000)] }],
    });

    await expect(matchCompiledWatchlistsInWindow({
      watchlists: [{
        watchlistId: "watchlist-1",
        watchlistLabel: "Existing notification",
        candidateFilters: { companyIds: ["company-1"] },
      }],
      windowStart: new Date("2026-08-24T00:00:00.000Z"),
      windowEnd: new Date("2026-08-31T00:00:00.000Z"),
      limitPerWatchlist: 20,
    })).resolves.toMatchObject({
      postings: [{ id: "legacy-hit" }],
    });

    const request = mocks.multiSearch.mock.calls[0]?.[0] as {
      searches: Array<{ sort_by: string }>;
    };
    expect(request.searches[0]?.sort_by).toBe("first_seen_at:desc");
  });

  it("fails closed when AF-2 requires stable order before activation", async () => {
    setTestEnv({ TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: undefined });

    await expect(matchCompiledWatchlistsInWindow({
      watchlists: [],
      windowStart: new Date("2026-08-24T00:00:00.000Z"),
      windowEnd: new Date("2026-08-31T00:00:00.000Z"),
      limitPerWatchlist: 20,
      requireStableOrder: true,
    })).rejects.toThrow("has not passed backfill readiness");
    expect(mocks.multiSearch).not.toHaveBeenCalled();
  });

  it("guards once, then multi-searches and deduplicates with all labels", async () => {
    const start = new Date("2026-08-24T00:00:00.000Z");
    const end = new Date("2026-08-31T00:00:00.000Z");
    const newestId = makeUuid(501);
    const sharedId = makeUuid(502);
    const olderId = makeUuid(503);
    const shared = posting(sharedId, end.getTime() / 1_000 - 20);
    const searchResults = {
      results: [
        {
          found: 2,
          hits: [posting(newestId, end.getTime() / 1_000 - 10), shared],
        },
        {
          found: 2,
          hits: [shared, posting(olderId, start.getTime() / 1_000 + 10)],
        },
      ],
    };
    mocks.multiSearch.mockImplementation((request: {
      searches: Array<{ sort_by: string }>;
    }) => {
      if (request.searches[0]?.sort_by.includes("missing_values")) {
        return {
          results: searchResults.results.map((result) => ({
            ...result,
            hits: result.hits.slice(0, 1),
          })),
        };
      }
      return searchResults;
    });

    const result = await matchCompiledWatchlistsInWindow({
      watchlists: [
        {
          watchlistId: "watchlist-1",
          watchlistLabel: "Backend",
          candidateFilters: {
            companyIds: ["company-1"],
            locationIds: [10],
            languages: ["de"],
          },
        },
        {
          watchlistId: "watchlist-2",
          watchlistLabel: "Remote",
          candidateFilters: {
            companyIds: [],
            anyCompany: true,
            workMode: ["remote"],
            languages: ["en"],
          },
        },
      ],
      windowStart: start,
      windowEnd: end,
      limitPerWatchlist: 20,
      requireStableOrder: true,
    });

    expect(mocks.multiSearch).toHaveBeenCalledTimes(2);
    const guardRequest = mocks.multiSearch.mock.calls[0]?.[0] as {
      searches: Array<{ sort_by: string; per_page: number; page: number }>;
    };
    expect(guardRequest.searches.every(
      (search) =>
        search.sort_by === "candidate_order_key(missing_values: first):asc" &&
        search.per_page === 1 &&
        search.page === 1,
    )).toBe(true);
    const request = mocks.multiSearch.mock.calls[1]?.[0] as {
      searches: Array<{ filter_by: string; sort_by: string }>;
    };
    expect(request.searches).toHaveLength(2);
    for (const search of request.searches) {
      expect(search.filter_by).toContain("is_active:true");
      expect(search.filter_by).toContain(
        `first_seen_at:>=${start.getTime() / 1_000}`,
      );
      expect(search.filter_by).toContain(
        `first_seen_at:<${end.getTime() / 1_000}`,
      );
      expect(search.sort_by).toBe(
        "first_seen_at:desc,candidate_order_key:asc",
      );
    }
    expect(request.searches[0]?.filter_by).toContain("location_ids:[10]");
    expect(request.searches[0]?.filter_by).toContain("locales:[de,_none]");
    expect(request.searches[1]?.filter_by).toContain("location_types:[remote]");

    expect(result.window).toEqual({
      windowStart: start.toISOString(),
      windowEnd: end.toISOString(),
      boundary: "[windowStart, windowEnd)",
    });
    expect(result.postings.map((value) => value.id)).toEqual([
      newestId,
      sharedId,
      olderId,
    ]);
    expect(
      result.postings.find((value) => value.id === sharedId)?.matchedWatchlists,
    ).toEqual([
      { id: "watchlist-1", label: "Backend" },
      { id: "watchlist-2", label: "Remote" },
    ]);
    expect(result.watchlists).toEqual([
      { id: "watchlist-1", label: "Backend", total: 2, returned: 2, truncated: false },
      { id: "watchlist-2", label: "Remote", total: 2, returned: 2, truncated: false },
    ]);
  });
});
