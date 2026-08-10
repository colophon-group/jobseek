import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getCompanyBySlug: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(_element: unknown, init: ResponseInit = {}) {
      super(new Uint8Array([9, 8, 7]), init);
    }
  },
}));

vi.mock("next/cache", () => ({
  cacheLife: vi.fn(),
}));

vi.mock("@/lib/services/company", () => ({
  getCompanyBySlug: mocks.getCompanyBySlug,
}));

vi.mock("@/lib/og/company-og-cache", () => ({
  companyOgCacheKey: vi.fn(() => "og/company/test/en/acme.png"),
  readCompanyOgCache: vi.fn(),
  shouldBypassCompanyOgCache: vi.fn(() => false),
  writeCompanyOgCache: vi.fn(),
}));

import * as ogImage from "../opengraph-image";
import * as renderer from "@/lib/og/render-company-og";

beforeEach(() => {
  mocks.getCompanyBySlug.mockReset();
});

describe("company opengraph image route rendering mode", () => {
  it("is dynamic because request-time R2 cache IO is intentional", () => {
    expect(ogImage.dynamic).toBe("force-dynamic");
    expect("generateStaticParams" in ogImage).toBe(false);
  });
});

describe("company opengraph image icon rendering", () => {
  it("only passes known raster icon URLs to next/og", () => {
    expect(
      renderer.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.png",
      ),
    ).toBe("https://jobseek-assets.colophon-group.org/companies/acme/icon.png");
    expect(
      renderer.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.jpg?version=1",
      ),
    ).toBe("https://jobseek-assets.colophon-group.org/companies/acme/icon.jpg?version=1");
    expect(
      renderer.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.jpeg",
      ),
    ).toBe("https://jobseek-assets.colophon-group.org/companies/acme/icon.jpeg");

    expect(
      renderer.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/graphcore/icon.svg",
      ),
    ).toBeNull();
    expect(
      renderer.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.webp",
      ),
    ).toBeNull();
    expect(renderer.getRenderableCompanyOgIconUrl("/local/icon.png")).toBeNull();
  });

  it("uses a deterministic fallback mark for unsupported remote icons", () => {
    expect(
      renderer.getCompanyOgIconRenderModel({
        name: "Graphcore",
        icon: "https://jobseek-assets.colophon-group.org/companies/graphcore/icon.svg",
      }),
    ).toEqual({ kind: "fallback", label: "GR" });

    expect(
      renderer.getCompanyOgIconRenderModel({
        name: "Acme Labs",
        icon: "https://jobseek-assets.colophon-group.org/companies/acme/icon.png",
      }),
    ).toEqual({
      kind: "image",
      src: "https://jobseek-assets.colophon-group.org/companies/acme/icon.png",
    });

    expect(
      renderer.getCompanyOgIconRenderModel({
        name: "Acme Labs",
        icon: null,
      }),
    ).toEqual({ kind: "none" });
  });

  it("preserves the existing PNG renderer on a durable-cache miss", async () => {
    mocks.getCompanyBySlug.mockResolvedValue({
      id: "co-1",
      name: "Acme Labs",
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
    });

    const result = await renderer.renderCompanyOgImage("acme", "en");

    expect(result.cacheable).toBe(true);
    expect(new Uint8Array(await result.response.arrayBuffer())).toEqual(
      new Uint8Array([9, 8, 7]),
    );
  });

  it("keeps unknown-company cards out of the durable cache", async () => {
    mocks.getCompanyBySlug.mockResolvedValue(null);

    const result = await renderer.renderCompanyOgImage("missing", "en");

    expect(result.cacheable).toBe(false);
    expect(new Uint8Array(await result.response.arrayBuffer())).toEqual(
      new Uint8Array([9, 8, 7]),
    );
  });
});
