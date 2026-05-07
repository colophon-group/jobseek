import { beforeEach, describe, it, expect, vi } from "vitest";
import { siteConfig } from "@/content/config";

const { dbExecuteMock, searchMock } = vi.hoisted(() => ({
  dbExecuteMock: vi.fn(),
  searchMock: vi.fn(),
}));

// Mock the database — the sitemap data layer queries the DB for
// companies (via Postgres fallback) and watchlists.
vi.mock("@/db", () => ({
  db: {
    execute: dbExecuteMock,
  },
}));

// Mock the cache. Memoize per-key within a single test so the sharded
// pipeline (planSitemapShards + per-shard renders) doesn't call the
// underlying fetcher repeatedly — that's what real Redis would do, and
// it matches how the production code expects to share work across
// shards within an ISR window.
const cacheStore = new Map<string, unknown>();
vi.mock("@/lib/cache", () => ({
  cached: async (
    key: string,
    fetcher: () => Promise<unknown>,
    options?: { skipIf?: (data: unknown) => boolean },
  ) => {
    if (cacheStore.has(key)) return cacheStore.get(key);
    const data = await fetcher();
    if (!options?.skipIf?.(data)) cacheStore.set(key, data);
    return data;
  },
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

import {
  planSitemapShards,
  renderSitemapShard,
  serializeUrlset,
  SITEMAP_TTL_SECONDS,
} from "../sitemap";

function typesensePage(slugs: string[], found: number) {
  return {
    found,
    hits: slugs.map((slug) => ({
      document: { slug, active_posting_count: 1 },
    })),
  };
}

/**
 * Render every shard returned by `planSitemapShards()` and concatenate
 * the entries — this is what the crawler ultimately gets across
 * /sitemap.xml and the /sitemap/<id>.xml shards.
 */
async function renderAllShards(): Promise<
  Awaited<ReturnType<typeof renderSitemapShard>>
> {
  const shards = await planSitemapShards();
  const all = await Promise.all(shards.map(({ id }) => renderSitemapShard(id)));
  return all.flat();
}

describe("sitemap data layer", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    dbExecuteMock.mockResolvedValue([]);
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense unavailable"));
    cacheStore.clear();
  });

  it("returns an array of entries", async () => {
    const result = await renderAllShards();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it("exposes a 1h ISR window for the cache wrappers (issue #2245)", () => {
    // Without ISR, every crawler hit runs the full handler (Postgres +
    // Typesense + serialization). Regression guard: a future refactor
    // must not silently change this constant.
    expect(SITEMAP_TTL_SECONDS).toBe(3600);
  });

  it("generates entries for all 4 locales", async () => {
    const result = await renderAllShards();
    const localesToCheck = ["en", "de", "fr", "it"];
    for (const locale of localesToCheck) {
      const hasLocale = result.some((entry) =>
        entry.url.includes(`/${locale}`)
      );
      expect(hasLocale, `should have entries for locale ${locale}`).toBe(true);
    }
  });

  it("each entry has required fields", async () => {
    const result = await renderAllShards();
    for (const entry of result) {
      expect(entry.url).toBeDefined();
      expect(typeof entry.url).toBe("string");
      expect(entry.url).toMatch(/^https?:\/\//);
      expect(entry.priority).toBeDefined();
      expect(entry.changeFrequency).toBeDefined();
    }
  });

  it("homepage entries have highest priority", async () => {
    const result = await renderAllShards();
    const homeEntries = result.filter((e) => e.url.match(/\/[a-z]{2}$/));
    for (const entry of homeEntries) {
      expect(entry.priority).toBe(1);
    }
  });

  it("static + explore lastModified are stable (not request-time, #2824)", async () => {
    // Previously every regen claimed `lastModified: new Date()`, which
    // Bing eventually discounts as a useless re-crawl signal. The
    // values must come from `siteConfig.seo.sitemap[i].lastModified`
    // and `siteConfig.seo.exploreLastModified`.
    //
    // Anchor on the actual static-page set: every URL listed in
    // `siteConfig.seo.sitemap` plus the explicit `/explore` entry.
    // A regex-based filter is too lenient — the previous version of
    // this test matched only homepage + /explore, which let regressions
    // on /about, /faq, /privacy-policy, /terms slip through.
    const before = Date.now();
    const result = await renderAllShards();
    const after = Date.now();
    const sitemapPaths = siteConfig.seo.sitemap.map((s) =>
      s.path === "/" ? "" : s.path,
    );
    const expectedSuffixes = [...sitemapPaths, "/explore"];
    const staticAndExplore = result.filter((e) =>
      expectedSuffixes.some((suffix) => {
        // URL pattern: ${siteConfig.url}/{locale}${suffix}
        const url = e.url;
        for (const locale of ["en", "de", "fr", "it"]) {
          const expected = `${siteConfig.url}/${locale}${suffix}`;
          if (url === expected) return true;
        }
        return false;
      }),
    );
    // 4 locales × (sitemap entries + /explore) — sanity that the filter
    // matched everything we expect, not just a subset.
    expect(staticAndExplore.length).toBe(
      (siteConfig.seo.sitemap.length + 1) * 4,
    );
    for (const entry of staticAndExplore) {
      const ts = entry.lastModified instanceof Date
        ? entry.lastModified.getTime()
        : new Date(entry.lastModified!).getTime();
      // A request-time `new Date()` would land between before/after.
      // A stable hardcoded date pre-dates the test run by months.
      expect(ts).toBeLessThan(before);
      // sanity: not in the future
      expect(ts).toBeLessThanOrEqual(after);
    }
  });

  it("hreflang map includes x-default pointing at /en (#2825)", async () => {
    const result = await renderAllShards();
    // Pick any entry with alternates — the homepage will do.
    const homepageEn = result.find((e) => e.url === "https://jseek.co/en");
    expect(homepageEn?.alternates?.languages).toBeDefined();
    expect(homepageEn?.alternates?.languages?.["x-default"]).toBe(
      "https://jseek.co/en",
    );
    // Non-/ paths should also carry x-default at /en/<path>.
    const aboutEn = result.find((e) => e.url === "https://jseek.co/en/about");
    expect(aboutEn?.alternates?.languages?.["x-default"]).toBe(
      "https://jseek.co/en/about",
    );
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

    const result = await renderAllShards();

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

    const result = await renderAllShards();

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

    const result = await renderAllShards();

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

    const result = await renderAllShards();
    const watchlistRowEntries = result.filter((entry) =>
      entry.url.includes("/curated-user/hot-list") || entry.url.includes("/regular-user/daily-list"),
    );

    expect(watchlistRowEntries).toHaveLength(8);
    expect(watchlistRowEntries.filter((entry) => entry.url.includes("/curated-user/hot-list"))).toHaveLength(4);
    expect(watchlistRowEntries.filter((entry) => entry.url.includes("/regular-user/daily-list"))).toHaveLength(4);
    expect(watchlistRowEntries.find((entry) => entry.url.endsWith("/en/curated-user/hot-list"))?.changeFrequency).toBe("daily");
    expect(watchlistRowEntries.find((entry) => entry.url.endsWith("/en/curated-user/hot-list"))?.priority).toBe(0.8);
    expect(watchlistRowEntries.find((entry) => entry.url.endsWith("/en/regular-user/daily-list"))?.changeFrequency).toBe("weekly");
    expect(watchlistRowEntries.find((entry) => entry.url.endsWith("/en/regular-user/daily-list"))?.priority).toBe(0.6);
    expect(watchlistRowEntries.find((entry) => entry.url.endsWith("/en/curated-user/hot-list"))?.alternates?.languages).toEqual({
      en: expect.stringContaining("/en/curated-user/hot-list"),
      de: expect.stringContaining("/de/curated-user/hot-list"),
      fr: expect.stringContaining("/fr/curated-user/hot-list"),
      it: expect.stringContaining("/it/curated-user/hot-list"),
      "x-default": expect.stringContaining("/en/curated-user/hot-list"),
    });
  });
});

describe("sitemap shards (issue #2646)", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    dbExecuteMock.mockResolvedValue([]);
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense unavailable"));
    cacheStore.clear();
  });

  it("declares one shard per 200-company batch plus one for static + watchlists", async () => {
    // 450 companies → 3 company shards (200 + 200 + 50) + 1 static-watchlist shard = 4 total.
    searchMock.mockResolvedValueOnce(typesensePage(
      Array.from({ length: 250 }, (_, i) => `company-${i + 1}`),
      450,
    )).mockResolvedValueOnce(typesensePage(
      Array.from({ length: 200 }, (_, i) => `company-${i + 251}`),
      450,
    ));

    const shards = await planSitemapShards();
    expect(shards).toEqual([
      { id: 0 },
      { id: 1 },
      { id: 2 },
      { id: 3 },
    ]);
  });

  it("shard 0 contains static pages and watchlists, no company URLs", async () => {
    dbExecuteMock.mockResolvedValueOnce([
      {
        user_slug: "alice",
        watchlist_slug: "ml-jobs",
        updated_at: new Date("2026-01-01T00:00:00Z"),
        is_curated: false,
      },
    ]);

    const entries = await renderSitemapShard(0);
    expect(entries.some((e) => e.url.includes("/company/"))).toBe(false);
    expect(entries.some((e) => e.url.endsWith("/en/explore"))).toBe(true);
    expect(entries.some((e) => e.url.endsWith("/en/alice/ml-jobs"))).toBe(true);
  });

  it("each company shard contains only its slice", async () => {
    searchMock.mockResolvedValueOnce(typesensePage(
      Array.from({ length: 250 }, (_, i) => `co-${i + 1}`),
      350,
    )).mockResolvedValueOnce(typesensePage(
      Array.from({ length: 100 }, (_, i) => `co-${i + 251}`),
      350,
    ));

    const shard1 = await renderSitemapShard(1); // companies 1..200
    expect(shard1.some((e) => e.url.endsWith("/en/company/co-1"))).toBe(true);
    expect(shard1.some((e) => e.url.endsWith("/en/company/co-200"))).toBe(true);
    expect(shard1.some((e) => e.url.endsWith("/en/company/co-201"))).toBe(false);

    // The memoized `cached()` mock satisfies shard 2's read without
    // re-invoking Typesense — same as production behavior under one
    // ISR window.
    const shard2 = await renderSitemapShard(2); // companies 201..350
    expect(shard2.some((e) => e.url.endsWith("/en/company/co-200"))).toBe(false);
    expect(shard2.some((e) => e.url.endsWith("/en/company/co-201"))).toBe(true);
    expect(shard2.some((e) => e.url.endsWith("/en/company/co-350"))).toBe(true);
  });

  it("returns at least one shard even when there are no companies", async () => {
    // Typesense empty + Postgres fallback empty: the planner still
    // must emit shard 0 (static + watchlists). Otherwise crawlers see
    // an empty sitemap index.
    searchMock.mockResolvedValueOnce({ found: 0, hits: [] });
    const shards = await planSitemapShards();
    expect(shards.length).toBeGreaterThanOrEqual(1);
    expect(shards[0]).toEqual({ id: 0 });
  });

  it("shard 0 still serves static + explore entries when watchlist DB throws (issue #2694)", async () => {
    // Production regression: when `cachedSitemapWatchlists()` threw,
    // the whole shard 0 function threw and Next.js served an empty
    // `<urlset/>` — wiping out even the hardcoded static + /explore
    // URLs.
    dbExecuteMock.mockReset();
    dbExecuteMock.mockRejectedValue(new Error("Postgres unavailable"));

    const entries = await renderSitemapShard(0);
    expect(entries.length).toBeGreaterThan(0);
    expect(entries.some((e) => e.url.endsWith("/en/explore"))).toBe(true);
    expect(entries.some((e) => e.url === "https://jseek.co/en")).toBe(true);
  });

  it("planSitemapShards emits only shard 0 when company fetch fails (issue #2694)", async () => {
    // Without this guard, a Typesense + Postgres dual outage would
    // cause the planner to throw — which the index handler would have
    // to catch separately, and any framework code calling the
    // planner would 500.
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense down"));
    dbExecuteMock.mockReset();
    dbExecuteMock.mockRejectedValue(new Error("Postgres also down"));

    const shards = await planSitemapShards();
    expect(shards).toEqual([{ id: 0 }]);
  });

  it("company shard returns [] (not throw) when company fetch fails", async () => {
    // Same hardening for non-zero shards: a Typesense+Postgres dual
    // outage shouldn't 500 the whole sitemap. Empty shard is fine —
    // crawlers retry; a 500 may de-rank.
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense down"));
    dbExecuteMock.mockReset();
    dbExecuteMock.mockRejectedValue(new Error("Postgres also down"));

    const entries = await renderSitemapShard(1);
    expect(entries).toEqual([]);
  });
});

describe("serializeUrlset", () => {
  it("emits a valid <urlset> with hreflang alternates", () => {
    const xml = serializeUrlset([
      {
        url: "https://jseek.co/en/company/foo",
        lastModified: new Date("2026-04-01T00:00:00Z"),
        changeFrequency: "daily",
        priority: 0.7,
        alternates: {
          languages: {
            en: "https://jseek.co/en/company/foo",
            de: "https://jseek.co/de/company/foo",
          },
        },
      },
    ]);
    expect(xml).toContain('<?xml version="1.0" encoding="UTF-8"?>');
    expect(xml).toContain('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"');
    expect(xml).toContain("<loc>https://jseek.co/en/company/foo</loc>");
    expect(xml).toContain("<lastmod>2026-04-01T00:00:00.000Z</lastmod>");
    expect(xml).toContain("<changefreq>daily</changefreq>");
    expect(xml).toContain("<priority>0.7</priority>");
    expect(xml).toContain('<xhtml:link rel="alternate" hreflang="en" href="https://jseek.co/en/company/foo"/>');
    expect(xml).toContain('<xhtml:link rel="alternate" hreflang="de" href="https://jseek.co/de/company/foo"/>');
  });

  it("escapes XML-significant characters in URLs", () => {
    const xml = serializeUrlset([
      {
        url: "https://jseek.co/en/foo?a=1&b=2",
        priority: 0.5,
        changeFrequency: "weekly",
        lastModified: new Date("2026-04-01T00:00:00Z"),
      },
    ]);
    expect(xml).toContain("?a=1&amp;b=2");
  });

  it("emits an empty urlset for no entries", () => {
    const xml = serializeUrlset([]);
    expect(xml).toContain("<urlset");
    expect(xml).toContain("</urlset>");
    expect(xml).not.toContain("<url>");
  });
});
