import type { NextConfig } from "next";
import withBundleAnalyzer from "@next/bundle-analyzer";

const nextConfig: NextConfig = {
  images: {
    // Cache optimized images (/_next/image) for 1 week instead of default 60s.
    // Vercel purges its CDN cache on every deploy, so stale images only persist
    // in individual browser caches until the TTL expires.
    minimumCacheTTL: 604800,
  },
  headers: async () => [
    {
      // Fonts never change between deploys — cache for 1 year
      source: "/fonts/:path*",
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
