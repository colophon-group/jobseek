// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import sharp from "sharp";
import { GET } from "./route";
import { emailCompanyIconUrl } from "@/lib/notifications/company-icon-url";

const source = "https://jobseek-assets.colophon-group.org/companies/msc/icon-" + "a".repeat(64) + ".webp";
const request = (src = source) => new Request(emailCompanyIconUrl(src, "https://jseek.co"));
const transparentLogo = Buffer.from('<svg width="40" height="80"><rect x="10" y="10" width="20" height="60" fill="#009999"/></svg>');
afterEach(() => vi.unstubAllGlobals());

describe("email company PNG endpoint", () => {
  it("preserves transparent WebP artwork on an opaque square PNG with padding", async () => {
    const webp = await sharp(transparentLogo).webp({ lossless: true }).toBuffer();
    const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array(webp)));
    vi.stubGlobal("fetch", fetch);
    const response = await GET(request());
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("image/png");
    expect(response.headers.get("Cache-Control")).toContain("immutable");
    expect(fetch.mock.calls[0]![0].href).toBe(source);
    expect(fetch.mock.calls[0]![1].redirect).toBe("error");
    expect(fetch.mock.calls[0]![1].signal).toBeInstanceOf(AbortSignal);
    const png = Buffer.from(await response.arrayBuffer());
    expect(await sharp(png).metadata()).toMatchObject({ format: "png", width: 96, height: 96, hasAlpha: false });
    const { data, info } = await sharp(png).raw().toBuffer({ resolveWithObject: true });
    const pixel = (x: number, y: number) => [...data.subarray((y * info.width + x) * info.channels, (y * info.width + x + 1) * info.channels)];
    expect(pixel(0, 0)).toEqual([245, 246, 243]);
    expect(pixel(48, 48)).toEqual([0, 153, 153]);
    expect(pixel(24, 48)).toEqual([245, 246, 243]);
    expect(pixel(48, 5)).toEqual([245, 246, 243]);
  });

  it("rasterizes vector icons and bounds caching for a historical mutable URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new Uint8Array(transparentLogo))));
    const response = await GET(request(source.replace(/icon-.*$/, "icon.svg")));
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("public, max-age=86400, s-maxage=86400");
    expect(await sharp(Buffer.from(await response.arrayBuffer())).metadata()).toMatchObject({ format: "png", width: 96, height: 96 });
  });

  it.each([
    "https://evil.example/companies/msc/icon.webp",
    "http://jobseek-assets.colophon-group.org/companies/msc/icon.webp",
    "https://user:pass@jobseek-assets.colophon-group.org/companies/msc/icon.webp",
    "https://jobseek-assets.colophon-group.org:444/companies/msc/icon.webp",
    "https://jobseek-assets.colophon-group.org/companies/msc/icon.webp?other=1",
    "https://jobseek-assets.colophon-group.org/companies/msc/icon.webp#fragment",
    "https://jobseek-assets.colophon-group.org/companies/msc/%69con.webp",
    "https://jobseek-assets.colophon-group.org/other/msc/icon.webp",
    "not a url",
  ])("rejects untrusted or noncanonical sources before fetching (%s)", async src => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const url = new URL("https://jseek.co/api/notifications/company-icon/v1.png");
    url.searchParams.set("src", src);
    const response = await GET(new Request(url));
    expect(response.status).toBe(400);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects extra or duplicate query parameters", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    for (const extra of ["&src=other", "&width=1024"]) {
      expect((await GET(new Request(request().url + extra))).status).toBe(400);
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([true, false])("bounds upstream bytes with and without Content-Length (%s)", async withLength => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new Uint8Array(5 * 1024 * 1024 + 1), {
      headers: withLength ? { "Content-Length": String(5 * 1024 * 1024 + 1) } : {},
    })));
    const response = await GET(request());
    expect(response.status).toBe(502);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it("does not cache upstream failures, invalid artwork or oversized decoded images", async () => {
    for (const upstream of [new Response(null, { status: 404 }), new Response("invalid image"),
      new Response('<svg width="10000" height="10000"><rect width="10000" height="10000"/></svg>')]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(upstream));
      const response = await GET(request());
      expect(response.status).toBe(502);
      expect(response.headers.get("Cache-Control")).toBe("no-store");
    }
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("timeout or disallowed redirect")));
    expect((await GET(request())).status).toBe(502);
  });
});
