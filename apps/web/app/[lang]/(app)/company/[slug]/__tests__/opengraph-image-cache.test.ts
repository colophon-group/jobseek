import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  companyOgCacheKey: vi.fn(() => "og/company/test/en/acme.png"),
  getCompanyBySlug: vi.fn(),
  readCompanyOgCache: vi.fn(),
  shouldBypassCompanyOgCache: vi.fn(),
  writeCompanyOgCache: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(_element: unknown, init: ResponseInit = {}) {
      super(new Uint8Array([9, 8, 7]), init);
    }
  },
}));

vi.mock("@/lib/actions/company", () => ({
  getCompanyBySlug: mocks.getCompanyBySlug,
}));

vi.mock("@/lib/og/company-og-cache", () => ({
  companyOgCacheKey: mocks.companyOgCacheKey,
  readCompanyOgCache: mocks.readCompanyOgCache,
  shouldBypassCompanyOgCache: mocks.shouldBypassCompanyOgCache,
  writeCompanyOgCache: mocks.writeCompanyOgCache,
}));

import OgImage from "../opengraph-image";

const company = {
  id: "co-1",
  name: "Acme",
  slug: "acme",
  icon: null,
  logo: null,
  website: "https://acme.example",
  description: "A company.",
  industryId: 1,
  industryName: "Software",
  employeeCountRange: null,
  foundedYear: null,
  activeJobCount: 12,
};

describe("company opengraph image cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.companyOgCacheKey.mockReturnValue("og/company/test/en/acme.png");
    mocks.getCompanyBySlug.mockResolvedValue(company);
    mocks.readCompanyOgCache.mockResolvedValue(null);
    mocks.shouldBypassCompanyOgCache.mockReturnValue(false);
  });

  it("returns R2 bytes on cache hit without loading company data", async () => {
    mocks.readCompanyOgCache.mockResolvedValue(new Uint8Array([1, 2, 3]));

    const response = await OgImage({
      params: Promise.resolve({ lang: "en", slug: "acme" }),
    });

    expect(mocks.companyOgCacheKey).toHaveBeenCalledWith("en", "acme");
    expect(mocks.readCompanyOgCache).toHaveBeenCalledWith("og/company/test/en/acme.png");
    expect(mocks.getCompanyBySlug).not.toHaveBeenCalled();
    expect(mocks.writeCompanyOgCache).not.toHaveBeenCalled();
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
  });

  it("renders and writes to R2 on cache miss", async () => {
    const response = await OgImage({
      params: Promise.resolve({ lang: "en", slug: "acme" }),
    });

    expect(mocks.getCompanyBySlug).toHaveBeenCalledWith("acme", "en");
    expect(mocks.writeCompanyOgCache).toHaveBeenCalledWith(
      "og/company/test/en/acme.png",
      new Uint8Array([9, 8, 7]),
    );
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([9, 8, 7]));
  });

  it("bypasses the R2 read when forced and overwrites the current key", async () => {
    mocks.shouldBypassCompanyOgCache.mockReturnValue(true);

    await OgImage({
      params: Promise.resolve({ lang: "en", slug: "acme" }),
    });

    expect(mocks.readCompanyOgCache).not.toHaveBeenCalled();
    expect(mocks.getCompanyBySlug).toHaveBeenCalledWith("acme", "en");
    expect(mocks.writeCompanyOgCache).toHaveBeenCalledWith(
      "og/company/test/en/acme.png",
      new Uint8Array([9, 8, 7]),
    );
  });
});
