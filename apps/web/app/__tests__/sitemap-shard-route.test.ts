import { beforeEach, describe, it, expect, vi } from "vitest";

const { renderShardMock, serializeMock } = vi.hoisted(() => ({
  renderShardMock: vi.fn(),
  serializeMock: vi.fn(),
}));

vi.mock("@/lib/sitemap", () => ({
  renderSitemapShard: renderShardMock,
  serializeUrlset: serializeMock,
}));

import { GET } from "../sitemap/[id]/route";

function paramsFor(id: string) {
  return { params: Promise.resolve({ id }) };
}

describe("/sitemap/<id>.xml shard handler (issue #2694)", () => {
  beforeEach(() => {
    renderShardMock.mockReset();
    serializeMock.mockReset();
    serializeMock.mockReturnValue("<urlset/>");
  });

  it("dispatches /sitemap/0.xml to renderSitemapShard(0)", async () => {
    renderShardMock.mockResolvedValue([{ url: "https://jseek.co/en" }]);

    const res = await GET(new Request("https://jseek.co/sitemap/0.xml"), paramsFor("0.xml"));

    expect(renderShardMock).toHaveBeenCalledWith(0);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/xml; charset=utf-8");
    expect(res.headers.get("cache-control")).toContain("s-maxage=3600");
  });

  it("parses multi-digit shard ids", async () => {
    renderShardMock.mockResolvedValue([]);
    await GET(new Request("https://jseek.co/sitemap/12.xml"), paramsFor("12.xml"));
    expect(renderShardMock).toHaveBeenCalledWith(12);
  });

  it("404s when the segment doesn't match <integer>.xml", async () => {
    const res = await GET(new Request("https://jseek.co/sitemap/foo"), paramsFor("foo"));
    expect(res.status).toBe(404);
    expect(renderShardMock).not.toHaveBeenCalled();
  });

  it("404s when the .xml suffix is missing", async () => {
    const res = await GET(new Request("https://jseek.co/sitemap/0"), paramsFor("0"));
    expect(res.status).toBe(404);
    expect(renderShardMock).not.toHaveBeenCalled();
  });

  it("returns the serialized XML body", async () => {
    renderShardMock.mockResolvedValue([{ url: "https://jseek.co/en" }]);
    serializeMock.mockReturnValue("<?xml version=\"1.0\"?><urlset/>");

    const res = await GET(new Request("https://jseek.co/sitemap/0.xml"), paramsFor("0.xml"));
    const body = await res.text();
    expect(body).toBe("<?xml version=\"1.0\"?><urlset/>");
  });
});
