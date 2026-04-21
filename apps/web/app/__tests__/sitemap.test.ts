import { beforeEach, describe, it, expect, vi } from "vitest";

const { dbExecuteMock, searchMock } = vi.hoisted(() => ({
  dbExecuteMock: vi.fn(),
  searchMock: vi.fn(),
}));

// Mock the database — sitemap queries the DB for companies and watchlists
vi.mock("@/db", () => ({
  db: {
    execute: dbExecuteMock,
  },
}));

// Mock the cache — passes through to the fetcher (no Redis in tests)
vi.mock("@/lib/cache", () => ({
  cached: (_key: string, fetcher: () => Promise<unknown>) => fetcher(),
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({
      documents: () => ({
        search: searchMock,
      }),
    }),
  }),
}));

import sitemap, { revalidate } from "../sitemap";

function typesensePage(slugs: string[], found: number) {
  return {
    found,
    hits: slugs.map((slug) => ({
      document: { slug, active_posting_count: 1 },
    })),
  };
}

describe("sitemap", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    dbExecuteMock.mockResolvedValue([]);
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense unavailable"));
  });

  it("returns an array of entries", async () => {
    const result = await sitemap();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it("exports `revalidate` so the response is CDN-cached as ISR (issue #2245)", () => {
    // Without `revalidate`, every crawler hit runs the full handler
    // (Postgres + Typesense + ~9 MB XML serialization). Regression
    // guard: a future refactor must not silently drop this export.
    expect(revalidate).toBe(3600);
  });

  it("generates entries for all 4 locales", async () => {
    const result = await sitemap();
    const locales = ["en", "de", "fr", "it"];
    for (const locale of locales) {
      const hasLocale = result.some((entry) =>
        entry.url.includes(`/${locale}`)
      );
      expect(hasLocale, `should have entries for locale ${locale}`).toBe(true);
    }
  });

  it("each entry has required fields", async () => {
    const result = await sitemap();
    for (const entry of result) {
      expect(entry.url).toBeDefined();
      expect(typeof entry.url).toBe("string");
      expect(entry.url).toMatch(/^https?:\/\//);
      expect(entry.priority).toBeDefined();
      expect(entry.changeFrequency).toBeDefined();
    }
  });

  it("homepage entries have highest priority", async () => {
    const result = await sitemap();
    const homeEntries = result.filter((e) => e.url.match(/\/[a-z]{2}$/));
    for (const entry of homeEntries) {
      expect(entry.priority).toBe(1);
    }
  });

  it("paginates Typesense company results so later pages reach the sitemap", async () => {
    searchMock
      .mockResolvedValueOnce(typesensePage(
        Array.from({ length: 250 }, (_, i) => `company-${i + 1}`),
        501,
      ))
      .mockResolvedValueOnce(typesensePage(
        Array.from({ length: 250 }, (_, i) => `company-${i + 251}`),
        501,
      ))
      .mockResolvedValueOnce(typesensePage(["company-501"], 501));

    const result = await sitemap();

    expect(searchMock).toHaveBeenCalledTimes(3);
    expect(searchMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ page: 1, per_page: 250 }),
    );
    expect(searchMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ page: 2, per_page: 250 }),
    );
    expect(searchMock).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ page: 3, per_page: 250 }),
    );
    expect(result.some((entry) => entry.url.endsWith("/en/company/company-251"))).toBe(true);
    expect(result.some((entry) => entry.url.endsWith("/de/company/company-251"))).toBe(true);
    expect(result.some((entry) => entry.url.endsWith("/en/company/company-501"))).toBe(true);
  });

  it("stops after an exact 250-result Typesense page", async () => {
    searchMock.mockResolvedValueOnce(typesensePage(
      Array.from({ length: 250 }, (_, i) => `company-${i + 1}`),
      250,
    ));

    const result = await sitemap();

    expect(searchMock).toHaveBeenCalledTimes(1);
    expect(searchMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ page: 1, per_page: 250 }),
    );
    expect(result.some((entry) => entry.url.endsWith("/en/company/company-250"))).toBe(true);
    expect(result.some((entry) => entry.url.endsWith("/en/company/company-251"))).toBe(false);
  });

  it("falls back to Postgres when a later Typesense page fails", async () => {
    searchMock
      .mockResolvedValueOnce(typesensePage(
        Array.from({ length: 250 }, (_, i) => `typesense-${i + 1}`),
        500,
      ))
      .mockRejectedValueOnce(new Error("Typesense page 2 failed"));

    dbExecuteMock.mockImplementation(async (query: unknown) => {
      const text = JSON.stringify(query);
      if (text.includes("FROM company c")) {
        return [
          { slug: "fallback-a", updated_at: new Date("2026-01-01T00:00:00Z") },
          { slug: "fallback-b", updated_at: new Date("2026-01-02T00:00:00Z") },
        ];
      }
      if (text.includes("FROM watchlist")) {
        return [];
      }
      return [];
    });

    const result = await sitemap();

    expect(searchMock).toHaveBeenCalledTimes(2);
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
    const fallbackCompanyEntries = result.filter((entry) =>
      entry.url.includes("/company/fallback-"),
    );
    expect(fallbackCompanyEntries).toHaveLength(8);
    expect(fallbackCompanyEntries.every((entry) => entry.url.includes("/company/fallback-"))).toBe(true);
  });

  it("preserves watchlist locale coverage and curated ordering metadata", async () => {
    searchMock.mockResolvedValueOnce({
      found: 0,
      hits: [],
    });

    dbExecuteMock.mockResolvedValueOnce([
      {
        user_slug: "curated-user",
        watchlist_slug: "hot-list",
        updated_at: new Date("2026-01-03T00:00:00Z"),
        is_curated: true,
      },
      {
        user_slug: "regular-user",
        watchlist_slug: "daily-list",
        updated_at: new Date("2026-01-02T00:00:00Z"),
        is_curated: false,
      },
    ]);

    const result = await sitemap();
    const watchlistEntries = result.filter((entry) =>
      entry.url.includes("/curated-user/hot-list") || entry.url.includes("/regular-user/daily-list"),
    );

    expect(watchlistEntries).toHaveLength(8);
    expect(watchlistEntries.filter((entry) => entry.url.includes("/curated-user/hot-list"))).toHaveLength(4);
    expect(watchlistEntries.filter((entry) => entry.url.includes("/regular-user/daily-list"))).toHaveLength(4);
    expect(watchlistEntries.find((entry) => entry.url.endsWith("/en/curated-user/hot-list"))?.changeFrequency).toBe("daily");
    expect(watchlistEntries.find((entry) => entry.url.endsWith("/en/curated-user/hot-list"))?.priority).toBe(0.8);
    expect(watchlistEntries.find((entry) => entry.url.endsWith("/en/regular-user/daily-list"))?.changeFrequency).toBe("weekly");
    expect(watchlistEntries.find((entry) => entry.url.endsWith("/en/regular-user/daily-list"))?.priority).toBe(0.6);
    expect(watchlistEntries.find((entry) => entry.url.endsWith("/en/curated-user/hot-list"))?.alternates?.languages).toEqual({
      en: expect.stringContaining("/en/curated-user/hot-list"),
      de: expect.stringContaining("/de/curated-user/hot-list"),
      fr: expect.stringContaining("/fr/curated-user/hot-list"),
      it: expect.stringContaining("/it/curated-user/hot-list"),
    });
  });
});
