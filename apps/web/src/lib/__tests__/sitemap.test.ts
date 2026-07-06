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

// Mock the cache. Memoize per-key within a single test — that's what
// real Redis would do, and it matches how the production code expects
// to share work across requests within an ISR window.
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
  buildSitemap,
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

describe("sitemap data layer", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    dbExecuteMock.mockResolvedValue([]);
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense unavailable"));
    cacheStore.clear();
  });

  it("returns an array of entries", async () => {
    const result = await buildSitemap();
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
    const result = await buildSitemap();
    const localesToCheck = ["en", "de", "fr", "it"];
    for (const locale of localesToCheck) {
      const hasLocale = result.some((entry) =>
        entry.url.includes(`/${locale}`)
      );
      expect(hasLocale, `should have entries for locale ${locale}`).toBe(true);
    }
  });

  it("each entry has required fields", async () => {
    const result = await buildSitemap();
    for (const entry of result) {
      expect(entry.url).toBeDefined();
      expect(typeof entry.url).toBe("string");
      expect(entry.url).toMatch(/^https?:\/\//);
      expect(entry.priority).toBeDefined();
      expect(entry.changeFrequency).toBeDefined();
    }
  });

  it("homepage entries have highest priority", async () => {
    const result = await buildSitemap();
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
    const result = await buildSitemap();
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
    const result = await buildSitemap();
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

  it("excludes /company/ URLs (#2821: companies left the index)", async () => {
    // Even with a healthy Typesense full of companies, the sitemap must
    // not surface any `/company/{slug}` URL. `noindex,follow` on the
    // page itself is the primary signal, but keeping company URLs out
    // of the sitemap closes the secondary discovery path.
    searchMock.mockResolvedValueOnce(typesensePage(
      Array.from({ length: 50 }, (_, i) => `company-${i + 1}`),
      50,
    ));

    const result = await buildSitemap();
    expect(result.some((entry) => entry.url.includes("/company/"))).toBe(false);
  });

  it("includes blog post URLs (#2828)", async () => {
    // Coverage for blog post entries — the suite reads real MDX files
    // under `src/content/blog`. A regression that drops blogPostEntries
    // from the urlset must fail this test rather than silently delisting
    // the posts. Asserting locale coverage on a known post (which ships
    // with all 4 translations) also exercises the per-post hreflang
    // alternates map (#2849-related).
    const result = await buildSitemap();
    const blogUrls = result.filter((e) => e.url.includes("/blog/"));
    expect(blogUrls.length).toBeGreaterThan(0);
    const welcomeUrls = blogUrls.filter((e) =>
      e.url.endsWith("/blog/welcome-to-the-job-seek-blog"),
    );
    // 4 locales × 1 post = 4 entries when fully translated.
    expect(welcomeUrls).toHaveLength(4);
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

    const result = await buildSitemap();
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

describe("buildSitemap — graceful degradation", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    dbExecuteMock.mockResolvedValue([]);
    searchMock.mockReset();
    searchMock.mockRejectedValue(new Error("Typesense unavailable"));
    cacheStore.clear();
  });

  it("still serves static + explore entries when watchlist DB throws (issue #2694)", async () => {
    // Production regression: when `cachedSitemapWatchlists()` threw,
    // the whole sitemap function threw and Next.js served an empty
    // `<urlset/>` — wiping out even the hardcoded static + /explore
    // URLs. buildSitemap must catch the watchlist fetcher and degrade.
    dbExecuteMock.mockReset();
    dbExecuteMock.mockRejectedValue(new Error("Postgres unavailable"));

    const entries = await buildSitemap();
    expect(entries.length).toBeGreaterThan(0);
    expect(entries.some((e) => e.url.endsWith("/en/explore"))).toBe(true);
    expect(entries.some((e) => e.url === "https://jseek.co/en")).toBe(true);
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
