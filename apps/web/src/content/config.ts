/**
 * Non-translatable site configuration.
 *
 * All translatable strings live in components via Lingui macros (<Trans>, t(), msg`...`).
 * This file holds structural/config data only: URLs, image paths, dimensions, asset keys, etc.
 *
 * When migrating components from the original frontend, move non-translatable
 * values here and replace translatable strings with Lingui macros inline.
 */

export const siteConfig = {
  url: "https://jobseek.com",
  domain: "jobseek.com",

  logo: {
    src: "/logo.svg",
    width: 32,
    height: 32,
  },

  logoWide: {
    src: "/logo-wide.svg",
    width: 140,
    height: 32,
  },

  seo: {
    disallow: ["/dashboard"],
    sitemap: "/sitemap.xml",
  },

  ui: {
    externalLinkTracking: {
      utmSource: "jobseek",
      utmMedium: "website",
    },
  },
} as const;
