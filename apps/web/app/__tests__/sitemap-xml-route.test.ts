import { beforeEach, describe, it, expect, vi } from "vitest";

const { planSitemapShardsMock, renderSitemapShardMock, serializeUrlsetMock } =
  vi.hoisted(() => ({
    planSitemapShardsMock: vi.fn(),
    renderSitemapShardMock: vi.fn(),
    serializeUrlsetMock: vi.fn(),
  }));

vi.mock("@/lib/sitemap", () => ({
  planSitemapShards: planSitemapShardsMock,
  renderSitemapShard: renderSitemapShardMock,
  serializeUrlset: serializeUrlsetMock,
}));

import { GET } from "../sitemap.xml/route";

/**
 * TEMPORARY suite: /sitemap.xml is a monolithic <urlset> while we
 * isolate why GSC reports "Couldn't fetch" on shard URLs. When the
 * sitemapindex is restored, replace this whole file with the prior
 * tests asserting <sitemapindex> output.
 */
describe("/sitemap.xml route handler (TEMPORARY monolithic urlset)", () => {
  beforeEach(() => {
    planSitemapShardsMock.mockReset();
    renderSitemapShardMock.mockReset();
    serializeUrlsetMock.mockReset();
    serializeUrlsetMock.mockImplementation(
      () => "<?xml version=\"1.0\"?><urlset/>",
    );
  });

  it("renders every shard, flattens the entries, and serializes once", async () => {
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }, { id: 1 }, { id: 2 }]);
    renderSitemapShardMock
      .mockResolvedValueOnce([{ url: "https://jseek.co/en" }])
      .mockResolvedValueOnce([
        { url: "https://jseek.co/en/company/a" },
        { url: "https://jseek.co/de/company/a" },
      ])
      .mockResolvedValueOnce([{ url: "https://jseek.co/en/company/b" }]);

    const res = await GET();

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/xml; charset=utf-8");
    expect(renderSitemapShardMock).toHaveBeenCalledTimes(3);
    expect(renderSitemapShardMock).toHaveBeenNthCalledWith(1, 0);
    expect(renderSitemapShardMock).toHaveBeenNthCalledWith(2, 1);
    expect(renderSitemapShardMock).toHaveBeenNthCalledWith(3, 2);
    expect(serializeUrlsetMock).toHaveBeenCalledWith([
      { url: "https://jseek.co/en" },
      { url: "https://jseek.co/en/company/a" },
      { url: "https://jseek.co/de/company/a" },
      { url: "https://jseek.co/en/company/b" },
    ]);
  });

  it("serves an empty <urlset/> rather than 5xx when planSitemapShards rejects", async () => {
    // Crawlers re-try retryable errors but de-rank on hard failures —
    // an empty 200 keeps the URL in good standing while we recover.
    planSitemapShardsMock.mockRejectedValue(new Error("planner down"));

    const res = await GET();

    expect(res.status).toBe(200);
    expect(renderSitemapShardMock).not.toHaveBeenCalled();
    expect(serializeUrlsetMock).toHaveBeenCalledWith([]);
  });

  it("serves an empty <urlset/> when a shard render rejects", async () => {
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }, { id: 1 }]);
    renderSitemapShardMock
      .mockResolvedValueOnce([{ url: "https://jseek.co/en" }])
      .mockRejectedValueOnce(new Error("shard 1 failed"));

    const res = await GET();

    expect(res.status).toBe(200);
    expect(serializeUrlsetMock).toHaveBeenCalledWith([]);
  });

  it("CDN-caches for 1h with a long stale-while-revalidate window", async () => {
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }]);
    renderSitemapShardMock.mockResolvedValue([]);

    const res = await GET();
    expect(res.headers.get("cache-control")).toContain("s-maxage=3600");
    expect(res.headers.get("cache-control")).toContain("stale-while-revalidate=86400");
  });
});
