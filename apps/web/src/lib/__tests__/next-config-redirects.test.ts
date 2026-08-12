import type { NextConfig } from "next";
import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "../../../next.config";
import { SITE_OG_PUBLIC_URL } from "../og/site-og-key";

describe("root asset redirects", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("canonicalizes browser-generated Apple icon variants", async () => {
    expect(typeof nextConfig).toBe("object");

    const redirects = await (nextConfig as NextConfig).redirects?.();

    expect(redirects).toContainEqual({
      source: "/apple-touch-icon-:variant.png",
      destination: "/apple-touch-icon.png",
      permanent: true,
    });
  });

  it("keeps previously shared site-wide OG URLs working from R2", async () => {
    const redirects = await (nextConfig as NextConfig).redirects?.();

    expect(redirects).toContainEqual({
      source: "/opengraph-image",
      destination: SITE_OG_PUBLIC_URL,
      permanent: true,
    });
    expect(redirects).toContainEqual({
      source: "/opengraph-image-:hash",
      destination: SITE_OG_PUBLIC_URL,
      permanent: true,
    });
  });

  it("redirects legacy company OG routes to the completed R2 namespace", async () => {
    vi.stubEnv("R2_DOMAIN_URL", "https://assets.example.test/");

    const config = nextConfig as NextConfig;
    const redirects = await config.redirects?.();
    const rendererVersion = config.env?.COMPANY_OG_RENDERER_VERSION;
    const sourceVersion = config.env?.COMPANY_OG_SOURCE_VERSION;

    expect(redirects).toContainEqual({
      source: "/:lang(en|de|fr|it)/company/:slug/opengraph-image-:hash",
      destination:
        `https://assets.example.test/og/company/${rendererVersion}/:lang/:slug.png?v=${sourceVersion}`,
      permanent: true,
    });
  });

  it("falls back safely when the configured R2 origin is unsafe", async () => {
    vi.stubEnv("R2_DOMAIN_URL", "http://assets.example.test/");

    const redirects = await (nextConfig as NextConfig).redirects?.();

    expect(redirects).toContainEqual({
      source: "/:lang(en|de|fr|it)/company/:slug/opengraph-image-:hash",
      destination: SITE_OG_PUBLIC_URL,
      permanent: true,
    });
  });

  it("preserves legacy dynamic OG URLs after isolating their renderers", async () => {
    const redirects = await (nextConfig as NextConfig).redirects?.();

    expect(redirects).toEqual(expect.arrayContaining([
      {
        source: "/:lang(en|de|fr|it)/blog/:slug/opengraph-image-:hash",
        destination: "/og/blog/:lang/:slug",
        permanent: true,
      },
      {
        source: "/:lang(en|de|fr|it)/how-we-index/opengraph-image-:hash",
        destination: "/og/how-we-index/:lang",
        permanent: true,
      },
      {
        source: "/:lang(en|de|fr|it)/:userSlug/:watchlistSlug/opengraph-image-:hash",
        destination: "/og/watchlist/:lang/:userSlug/:watchlistSlug",
        permanent: true,
      },
    ]));
  });

  it("retains the runtime-configured IndexNow proof rewrite", async () => {
    vi.stubEnv("INDEXNOW_KEY", "indexnow-verification-token");

    const rewrites = await (nextConfig as NextConfig).rewrites?.();

    expect(rewrites).toContainEqual({
      source: "/indexnow-verification-token.txt",
      destination: "/indexnow-key.txt",
    });
  });
});
