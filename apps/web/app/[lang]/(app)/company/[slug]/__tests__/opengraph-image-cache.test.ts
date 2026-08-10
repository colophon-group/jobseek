import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  companyOgCacheKey: vi.fn(() => "og/company/test/en/acme.png"),
  readCompanyOgCache: vi.fn(),
  renderCompanyOgImage: vi.fn(),
  shouldBypassCompanyOgCache: vi.fn(),
  writeCompanyOgCache: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("@/lib/og/company-og-cache", () => ({
  companyOgCacheKey: mocks.companyOgCacheKey,
  readCompanyOgCache: mocks.readCompanyOgCache,
  shouldBypassCompanyOgCache: mocks.shouldBypassCompanyOgCache,
  writeCompanyOgCache: mocks.writeCompanyOgCache,
}));

vi.mock("@/lib/og/render-company-og", () => ({
  renderCompanyOgImage: mocks.renderCompanyOgImage,
}));

import OgImage from "../opengraph-image";

describe("company opengraph image cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.companyOgCacheKey.mockReturnValue("og/company/test/en/acme.png");
    mocks.readCompanyOgCache.mockResolvedValue(null);
    mocks.renderCompanyOgImage.mockResolvedValue({
      response: new Response(new Uint8Array([9, 8, 7])),
      cacheable: true,
    });
    mocks.shouldBypassCompanyOgCache.mockReturnValue(false);
  });

  it("returns R2 bytes without loading the renderer", async () => {
    mocks.readCompanyOgCache.mockResolvedValue(new Uint8Array([1, 2, 3]));

    const response = await OgImage({
      params: Promise.resolve({ lang: "en", slug: "acme" }),
    });

    expect(mocks.companyOgCacheKey).toHaveBeenCalledWith("en", "acme");
    expect(mocks.readCompanyOgCache).toHaveBeenCalledWith("og/company/test/en/acme.png");
    expect(mocks.renderCompanyOgImage).not.toHaveBeenCalled();
    expect(mocks.writeCompanyOgCache).not.toHaveBeenCalled();
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
  });

  it("lazy-renders and writes to R2 on cache miss", async () => {
    const response = await OgImage({
      params: Promise.resolve({ lang: "en", slug: "acme" }),
    });

    expect(mocks.renderCompanyOgImage).toHaveBeenCalledWith("acme", "en");
    expect(mocks.writeCompanyOgCache).toHaveBeenCalledWith(
      "og/company/test/en/acme.png",
      new Uint8Array([9, 8, 7]),
    );
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([9, 8, 7]));
  });

  it("does not cache the not-found card", async () => {
    mocks.renderCompanyOgImage.mockResolvedValue({
      response: new Response(new Uint8Array([9, 8, 7])),
      cacheable: false,
    });

    await OgImage({
      params: Promise.resolve({ lang: "en", slug: "missing" }),
    });

    expect(mocks.writeCompanyOgCache).not.toHaveBeenCalled();
  });

  it("bypasses the R2 read when forced and overwrites the current key", async () => {
    mocks.shouldBypassCompanyOgCache.mockReturnValue(true);

    await OgImage({
      params: Promise.resolve({ lang: "en", slug: "acme" }),
    });

    expect(mocks.readCompanyOgCache).not.toHaveBeenCalled();
    expect(mocks.renderCompanyOgImage).toHaveBeenCalledWith("acme", "en");
    expect(mocks.writeCompanyOgCache).toHaveBeenCalledWith(
      "og/company/test/en/acme.png",
      new Uint8Array([9, 8, 7]),
    );
  });
});
