import { beforeEach, describe, it, expect, vi } from "vitest";

const { planSitemapShardsMock } = vi.hoisted(() => ({
  planSitemapShardsMock: vi.fn(),
}));

vi.mock("@/lib/sitemap", () => ({
  planSitemapShards: planSitemapShardsMock,
}));

vi.mock("@/content/config", () => ({
  siteConfig: { url: "https://jseek.co" },
}));

import { GET } from "../sitemap.xml/route";

describe("/sitemap.xml route handler (issue #2694)", () => {
  beforeEach(() => {
    planSitemapShardsMock.mockReset();
  });

  it("emits a sitemapindex listing every shard returned by planSitemapShards", async () => {
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }, { id: 1 }, { id: 2 }]);

    const res = await GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/xml; charset=utf-8");

    const body = await res.text();
    expect(body).toContain('<?xml version="1.0" encoding="UTF-8"?>');
    expect(body).toContain('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
    expect(body).toContain("<loc>https://jseek.co/sitemap/0.xml</loc>");
    expect(body).toContain("<loc>https://jseek.co/sitemap/1.xml</loc>");
    expect(body).toContain("<loc>https://jseek.co/sitemap/2.xml</loc>");
    expect(body).toContain("</sitemapindex>");
  });

  it("emits a <lastmod> on every <sitemap> entry so crawlers re-walk children", async () => {
    // Without <lastmod>, Google can stick on a stale view of the
    // index for days. The value just needs to be a valid W3C
    // datetime; we use the current time bounded by the 1h cache TTL.
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }, { id: 1 }]);

    const res = await GET();
    const body = await res.text();

    const sitemapEntries = body.match(/<sitemap>[\s\S]*?<\/sitemap>/g) ?? [];
    expect(sitemapEntries).toHaveLength(2);
    for (const entry of sitemapEntries) {
      expect(entry).toMatch(/<lastmod>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z<\/lastmod>/);
    }
  });

  it("falls back to a single-shard index when planSitemapShards throws", async () => {
    // Defense-in-depth: planSitemapShards already swallows fetcher
    // errors, but if a future change makes it throw the index must
    // still serve a valid XML pointing at shard 0 (which serves the
    // hardcoded static URLs).
    planSitemapShardsMock.mockRejectedValue(new Error("backends down"));

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("<loc>https://jseek.co/sitemap/0.xml</loc>");
    expect(body).not.toContain("<loc>https://jseek.co/sitemap/1.xml</loc>");
  });

  it("emits at least shard 0 when planSitemapShards returns []", async () => {
    planSitemapShardsMock.mockResolvedValue([]);

    const res = await GET();
    const body = await res.text();
    expect(body).toContain("<loc>https://jseek.co/sitemap/0.xml</loc>");
  });

  it("CDN-caches for 1h with a long stale-while-revalidate window", async () => {
    // Cache-Control is the single source of truth — segment-config
    // `revalidate` would conflict with this explicit header on a
    // Route Handler, so it's intentionally not exported.
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }]);
    const res = await GET();
    expect(res.headers.get("cache-control")).toContain("s-maxage=3600");
    expect(res.headers.get("cache-control")).toContain("stale-while-revalidate=86400");
  });
});
