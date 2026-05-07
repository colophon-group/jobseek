import type { NextConfig } from "next";
import withBundleAnalyzer from "@next/bundle-analyzer";
import path from "node:path";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  // Stable Cache Components / Partial Prerendering (Next 16). Static
  // shells prerender; `'use cache'` content caches per region; dynamic
  // subtrees stream inside Suspense. See apps/web/docs/cache-components.md
  // and #2835 for the conventions.
  cacheComponents: true,
  images: {
    // Cache optimized images for 1 year. Company logos rarely change, and
    // Vercel purges its CDN cache on every deploy anyway.
    minimumCacheTTL: 31536000,
    // 100% of company icons/logos currently resolve to R2 or DDG. The
    // historical `**` wildcard fallback let any hostname expand source
    // cardinality for the image optimizer (each distinct host is a new
    // transformation source), so it's scoped to the two known hosts.
    // If a new host is ever needed, add it explicitly — do not restore
    // the wildcard.
    remotePatterns: [
      { hostname: "jobseek-assets.colophon-group.org" },
      { hostname: "icons.duckduckgo.com" },
    ],
    // Bound the cache-key cardinality. A transformation is billed once per
    // unique (url, w, q, format). Default lists give 8 deviceSizes × 8
    // imageSizes — anything along the device ladder is reachable, doubling
    // (or worse) the per-source variant fan-out for no visible win on
    // sources that are pixel-bound at the source resolution.
    //
    // qualities=[75]: lock to the default; no caller passes quality={N}.
    // formats: explicit single format (Next 15+ default is webp; AVIF
    //   would double transformation count for negligible byte savings on
    //   the largest optimizer surfaces — 156-304 KB Features 1200×630
    //   screenshot PNGs and 550-1024 px PublicDomainArt PNGs.
    // deviceSizes: 8→4. The largest source emitted through the optimizer
    //   is the 1200×630 Features screenshot. deviceSize > 1200 only
    //   upscales (blurry, no extra information). 1920 keeps a small
    //   buffer for moderate retina without paying for 2048/3840 variants.
    // imageSizes: 8→6. Each existing company-icon width (16/20/24/28/32/36)
    //   snaps to a rung still on this list (16, 32, 32, 32, 32, 48 — every
    //   width has a smallest-≥ match), so the narrower list is safe for
    //   the current call sites and is also forward-compatible with #2867
    //   (which moves icons to `unoptimized`, dropping their imageSize use
    //   entirely).
    qualities: [75],
    formats: ["image/webp"],
    deviceSizes: [640, 1080, 1200, 1920],
    imageSizes: [16, 32, 48, 64, 96, 128],
  },
  rewrites: async () => {
    const key = process.env.INDEXNOW_KEY;
    return key ? [{ source: `/${key}.txt`, destination: "/indexnow-key.txt" }] : [];
  },
  redirects: async () => [
    { source: "/:lang(en|de|fr|it)/app", destination: "/:lang/explore", permanent: true },
    { source: "/:lang(en|de|fr|it)/app/saved", destination: "/:lang/my-jobs", permanent: true },
    { source: "/:lang(en|de|fr|it)/saved", destination: "/:lang/my-jobs", permanent: true },
    { source: "/:lang(en|de|fr|it)/app/settings/:path*", destination: "/:lang/settings/:path*", permanent: true },
    { source: "/:lang(en|de|fr|it)/app/watchlists", destination: "/:lang/watchlists", permanent: true },
    { source: "/:lang(en|de|fr|it)/app/progress", destination: "/:lang/progress", permanent: true },
  ],
  headers: async () => [
    {
      // Optimized remote images (company logos) — cache aggressively.
      // Logos rarely change; Vercel CDN is purged on deploy.
      source: "/_next/image",
      headers: [
        {
          key: "Cache-Control",
          value: "public, max-age=31536000, stale-while-revalidate=604800, immutable",
        },
      ],
    },
    {
      // Fonts never change between deploys — cache for 1 year
      source: "/fonts/:path*",
      headers: [
        { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
      ],
    },
    {
      // Flag SVGs never change — cache for 1 year
      source: "/flags/:path*",
      headers: [
        { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
      ],
    },
    {
      // Static images and SVGs — cache for 1 week
      source: "/publicdomain/:path*",
      headers: [
        { key: "Cache-Control", value: "public, max-age=604800" },
      ],
    },
    {
      source: "/:path*.svg",
      headers: [
        { key: "Cache-Control", value: "public, max-age=604800" },
      ],
    },
    {
      source: "/:path*.png",
      headers: [
        { key: "Cache-Control", value: "public, max-age=604800" },
      ],
    },
    {
      // Favicon and web manifest — rarely change
      source: "/(favicon.ico|site.webmanifest|apple-touch-icon.png|android-chrome-:path*)",
      headers: [
        { key: "Cache-Control", value: "public, max-age=604800" },
      ],
    },
  ],
  devIndicators: false,
  turbopack: {
    rules: {
      "*.po": {
        loaders: ["@lingui/loader"],
        as: "*.js",
      },
    },
  },
  webpack: (config) => {
    config.module.rules.push({
      test: /\.po$/,
      use: {
        loader: "@lingui/loader",
      },
    });
    return config;
  },
};

const withAnalyzer = withBundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

export default withAnalyzer(nextConfig);
