import type { NextConfig } from "next";
import withBundleAnalyzer from "@next/bundle-analyzer";
import path from "node:path";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  images: {
    // Cache optimized images for 1 year. Company logos rarely change, and
    // Vercel purges its CDN cache on every deploy anyway.
    minimumCacheTTL: 31536000,
    remotePatterns: [
      { hostname: "jobseek-assets.colophon-group.org" },
      { hostname: "icons.duckduckgo.com" },
      { hostname: "**" }, // fallback logos hosted directly on company domains
    ],
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
