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
    ],
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
      // Security headers applied to all routes.
      source: "/:path*",
      headers: [
        // Prevent the page from being embedded in iframes (clickjacking protection).
        { key: "X-Frame-Options", value: "SAMEORIGIN" },
        // Prevent MIME-type sniffing.
        { key: "X-Content-Type-Options", value: "nosniff" },
        // Control referrer information sent with outbound requests.
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        // Restrict access to browser features.
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        // Content Security Policy.
        // - default-src 'self': only allow content from this origin by default.
        // - script-src 'self' 'unsafe-inline': Next.js requires unsafe-inline for
        //   its runtime bootstrap script; 'unsafe-eval' is intentionally omitted.
        // - style-src 'self' 'unsafe-inline': CSS-in-JS and inline styles.
        // - img-src 'self' data: blob: + trusted CDN for company logos.
        // - connect-src 'self': XHR/fetch only to own origin.
        // - font-src 'self': self-hosted fonts only.
        // - frame-ancestors 'self': duplicate of X-Frame-Options for CSP-aware browsers.
        {
          key: "Content-Security-Policy",
          value: [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https://jobseek-assets.colophon-group.org",
            "connect-src 'self'",
            "font-src 'self'",
            "frame-ancestors 'self'",
            "base-uri 'self'",
            "form-action 'self'",
          ].join("; "),
        },
      ],
    },
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
