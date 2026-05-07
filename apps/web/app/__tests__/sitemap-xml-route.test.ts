import { beforeEach, describe, it, expect, vi } from "vitest";

const { buildSitemapMock, serializeUrlsetMock } =
  vi.hoisted(() => ({
    buildSitemapMock: vi.fn(),
    serializeUrlsetMock: vi.fn(),
  }));

vi.mock("@/lib/sitemap", () => ({
  buildSitemap: buildSitemapMock,
  serializeUrlset: serializeUrlsetMock,
}));

import { GET } from "../sitemap.xml/route";

describe("/sitemap.xml route handler", () => {
  beforeEach(() => {
    buildSitemapMock.mockReset();
    serializeUrlsetMock.mockReset();
    serializeUrlsetMock.mockImplementation(
      () => "<?xml version=\"1.0\"?><urlset/>",
    );
  });

  it("serves the buildSitemap output as XML", async () => {
    const entries = [
      { url: "https://jseek.co/en" },
      { url: "https://jseek.co/en/explore" },
    ];
    buildSitemapMock.mockResolvedValue(entries);

    const res = await GET();

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/xml; charset=utf-8");
    expect(serializeUrlsetMock).toHaveBeenCalledWith(entries);
  });

  it("CDN-caches for 1h with a long stale-while-revalidate window", async () => {
    buildSitemapMock.mockResolvedValue([]);

    const res = await GET();
    expect(res.headers.get("cache-control")).toContain("s-maxage=3600");
    expect(res.headers.get("cache-control")).toContain("stale-while-revalidate=86400");
  });
});
