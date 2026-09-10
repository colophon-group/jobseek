import { beforeEach, describe, it, expect, vi } from "vitest";
import { siteConfig } from "@/content/config";

const { dbExecuteMock } = vi.hoisted(() => ({
  dbExecuteMock: vi.fn(),
}));

// Public watchlists are account-private resources and must not pull the
// database back into sitemap generation.
vi.mock("@/db", () => ({
  db: {
    execute: dbExecuteMock,
  },
}));

import {
  buildSitemap,
  serializeUrlset,
} from "../sitemap";

describe("sitemap data layer", () => {
  beforeEach(() => {
    dbExecuteMock.mockReset();
    dbExecuteMock.mockResolvedValue([]);
  });

  it("returns an array of entries", async () => {
    const result = await buildSitemap();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
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

  it("does not query or publish legacy public watchlists", async () => {
    dbExecuteMock.mockResolvedValue([
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
    expect(dbExecuteMock).not.toHaveBeenCalled();
    expect(
      result.some((entry) =>
        entry.url.includes("/curated-user/hot-list")
        || entry.url.includes("/regular-user/daily-list"),
      ),
    ).toBe(false);
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
