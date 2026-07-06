import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(_element: unknown, init: ResponseInit = {}) {
      super(new Uint8Array([9, 8, 7]), init);
    }
  },
}));

vi.mock("@/lib/actions/company", () => ({
  getCompanyBySlug: vi.fn(),
}));

vi.mock("@/lib/og/company-og-cache", () => ({
  companyOgCacheKey: vi.fn(() => "og/company/test/en/acme.png"),
  readCompanyOgCache: vi.fn(),
  shouldBypassCompanyOgCache: vi.fn(() => false),
  writeCompanyOgCache: vi.fn(),
}));

import * as ogImage from "../opengraph-image";

describe("company opengraph image route rendering mode", () => {
  it("is dynamic because request-time R2 cache IO is intentional", () => {
    expect(ogImage.dynamic).toBe("force-dynamic");
    expect("generateStaticParams" in ogImage).toBe(false);
  });
});

describe("company opengraph image icon rendering", () => {
  it("only passes known raster icon URLs to next/og", () => {
    expect(
      ogImage.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.png",
      ),
    ).toBe("https://jobseek-assets.colophon-group.org/companies/acme/icon.png");
    expect(
      ogImage.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.jpg?version=1",
      ),
    ).toBe("https://jobseek-assets.colophon-group.org/companies/acme/icon.jpg?version=1");
    expect(
      ogImage.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.jpeg",
      ),
    ).toBe("https://jobseek-assets.colophon-group.org/companies/acme/icon.jpeg");

    expect(
      ogImage.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/graphcore/icon.svg",
      ),
    ).toBeNull();
    expect(
      ogImage.getRenderableCompanyOgIconUrl(
        "https://jobseek-assets.colophon-group.org/companies/acme/icon.webp",
      ),
    ).toBeNull();
    expect(ogImage.getRenderableCompanyOgIconUrl("/local/icon.png")).toBeNull();
  });

  it("uses a deterministic fallback mark for unsupported remote icons", () => {
    expect(
      ogImage.getCompanyOgIconRenderModel({
        name: "Graphcore",
        icon: "https://jobseek-assets.colophon-group.org/companies/graphcore/icon.svg",
      }),
    ).toEqual({ kind: "fallback", label: "GR" });

    expect(
      ogImage.getCompanyOgIconRenderModel({
        name: "Acme Labs",
        icon: "https://jobseek-assets.colophon-group.org/companies/acme/icon.png",
      }),
    ).toEqual({
      kind: "image",
      src: "https://jobseek-assets.colophon-group.org/companies/acme/icon.png",
    });

    expect(
      ogImage.getCompanyOgIconRenderModel({
        name: "Acme Labs",
        icon: null,
      }),
    ).toEqual({ kind: "none" });
  });
});
