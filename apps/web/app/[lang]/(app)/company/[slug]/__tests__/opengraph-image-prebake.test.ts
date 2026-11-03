import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(_element: unknown, init: ResponseInit = {}) {
      super(new Uint8Array([9, 8, 7]), init);
    }
  },
}));

import * as renderer from "@/lib/og/company-og-card";

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

});
