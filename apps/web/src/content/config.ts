import { SITE_OG_PUBLIC_URL } from "@/lib/og/site-og-key";

/**
 * Non-translatable site configuration.
 *
 * All translatable strings live in components via Lingui macros (<Trans>, t(), msg`...`).
 * This file holds structural/config data only: URLs, image paths, dimensions, asset keys, etc.
 *
 * When migrating components from the original frontend, move non-translatable
 * values here and replace translatable strings with Lingui macros inline.
 */

export type CropInsets = {
  top?: number;
  right?: number;
  bottom?: number;
  left?: number;
};

export type PublicDomainAsset = {
  href?: string;
  light?: string;
  dark?: string;
  alt: string;
  width: number;
  height: number;
  title?: string;
  author?: string;
  date?: string;
  link?: string;
  crop?: CropInsets;
};

export const siteConfig = {
  url: "https://jseek.co",
  domain: "jseek.co",
  repoUrl: "https://github.com/colophon-group/jobseek",
  creator: "Viktor Shcherbakov",
  creatorUrl: "https://github.com/viktor-shcherb",

  social: {
    linkedin: { href: "https://www.linkedin.com/company/jseek/", external: true },
  },

  logo: {
    src: "/js_logo_black_circle.svg",
    width: 500,
    height: 500,
  },

  logoWide: {
    light: "/js_wide_logo_black.svg",
    dark: "/js_wide_logo_white.svg",
    width: 144,
    height: 36,
  },

  ogImage: {
    url: SITE_OG_PUBLIC_URL,
    width: 1200,
    height: 630,
  },

  nav: {
    about: { href: "/about" },
    features: { href: "/#features" },
    pricing: { href: "/#pricing" },
    faq: { href: "/faq" },
    // Header nav slot for product+process content. Originally pointed
    // at the static `/how-we-index` page (#2828: replaced by a more
    // engaging /blog hub that includes the indexing-policy story as a
    // post). The static page stays reachable from the about page +
    // footer for the readers who want the spec-style summary.
    blog: { href: "/blog" },
    license: { href: "/license" },
    login: { href: "/sign-in" },
    app: { href: "/explore" },
    settings: { href: "/settings" },
    dashboard: { href: "/dashboard" },
  },

  hero: {
    art: {
      assetKey: "the_astrologer" as const,
      focus: { x: 0, y: 35 },
    },
  },

  features: {
    anchorId: "features",
    sections: [
      {
        screenshot: {
          light: "/screenshots/{lang}/feature1-light.png",
          dark: "/screenshots/{lang}/feature1-dark.png",
          width: 1200,
          height: 630,
        },
        pointIcons: ["source", "filters", "alerts"] as const,
      },
      {
        screenshot: {
          light: "/screenshots/{lang}/feature2-light.png",
          dark: "/screenshots/{lang}/feature2-dark.png",
          width: 1200,
          height: 630,
        },
        pointIcons: ["tracking", "interviews", "stats"] as const,
      },
      {
        screenshot: {
          light: "/screenshots/{lang}/feature3-2026-09-light.png",
          dark: "/screenshots/{lang}/feature3-2026-09-dark.png",
          width: 1200,
          height: 630,
        },
        pointIcons: ["curate", "companies", "share"] as const,
      },
    ],
  },

  pricing: {
    anchorId: "pricing",
    free: { href: "/sign-up" },
    pro: { href: "/sign-up" },
  },

  indexing: {
    contactEmail: "business@colophon-group.org",
    ossRepoUrl: "https://github.com/colophon-group/jobseek-indexing",
    anchors: {
      overview: "indexing-overview",
      assurances: "indexing-assurances",
      ingestion: "indexing-ingestion",
      optOut: "indexing-opt-out",
      automation: "indexing-automation",
      oss: "indexing-oss",
      outreach: "indexing-outreach",
    },
  },

  license: {
    hero: {
      art: {
        assetKey: "the_judge" as const,
        focus: { x: 0, y: 20 },
      },
    },
    anchors: {
      overview: "license-overview",
      code: "license-code",
      data: "license-data",
      contact: "license-contact",
    },
  },

  privacy: {
    lastUpdated: "2026-09-27",
    hero: {
      art: {
        assetKey: "the_advocate" as const,
        focus: { x: 0, y: 25 },
      },
    },
  },

  terms: {
    lastUpdated: "2026-09-27",
    hero: {
      art: {
        assetKey: "the_king" as const,
        focus: { x: 0, y: 20 },
      },
    },
  },

  about: {
    hero: {
      art: {
        assetKey: "adam_tills_the_soil" as const,
        focus: { x: 0, y: 30 },
      },
    },
  },

  homepageArt: {
    assetKey: "the_miser" as const,
    focus: { x: 45, y: 40 },
  },

  seo: {
    // Public HTML shells stay crawlable so search engines can read noindex.
    // Authentication/authorization, not robots.txt, protects account data.
    disallow: ["/api/auth/", "/api/admin/", "/api/paddle/"],
    // Only pages users actually search for. Legal/policy pages
    // (license, privacy-policy, terms, how-we-index) are reached from
    // the footer of every page and are noindex per #2822 — adding
    // them here would round-trip the discovery for no SEO benefit.
    //
    // Bump `lastModified` per entry whenever the page's bot-visible
    // content (copy, hero, layout) substantively changes. Stable dates
    // are an honest re-crawl signal for Bing — `new Date()` on every
    // regen would always say "modified now" and the engine eventually
    // discounts the signal (#2824).
    sitemap: [
      { path: "/", changeFrequency: "weekly", priority: 1, lastModified: "2026-09-28" },
      { path: "/about", changeFrequency: "monthly", priority: 0.7, lastModified: "2026-09-28" },
      { path: "/faq", changeFrequency: "monthly", priority: 0.7, lastModified: "2026-09-28" },
      { path: "/job-alerts", changeFrequency: "monthly", priority: 0.8, lastModified: "2026-09-28" },
      { path: "/job-application-tracker", changeFrequency: "monthly", priority: 0.8, lastModified: "2026-09-28" },
      { path: "/narrowed", changeFrequency: "monthly", priority: 0.8, lastModified: "2026-09-28" },
      // Blog index. Per-post URLs are emitted separately by
      // `blogPostEntries` in `apps/web/src/lib/sitemap.ts` (#2828).
      { path: "/blog", changeFrequency: "weekly", priority: 0.7, lastModified: "2026-09-28" },
    ],
  },

  footer: {
    links: {
      github: { href: "https://github.com/colophon-group/jobseek", external: true },
      contact: { href: "mailto:business@colophon-group.org", external: true },
      blog: { href: "/blog", external: false },
      api: { href: "/api/openapi.json", external: false },
      license: { href: "/license", external: false },
      privacy: { href: "/privacy-policy", external: false },
      terms: { href: "/terms", external: false },
    },
  },

} as const;

export const publicDomainAssets: Record<string, PublicDomainAsset> = {
  the_woodcutter: {
    href: "/publicdomain/master/the_woodcutter.jpg",
    light: "/publicdomain/the_woodcutter_dark.png",
    dark: "/publicdomain/the_woodcutter_light.png",
    width: 1561,
    height: 2055,
    crop: { top: 370, right: 45, bottom: 80, left: 45 },
    alt: "A craftsman carving a woodblock in his workshop",
    title: "The Woodcutter",
    author: "Jost Amman",
    date: "1568",
    link: "https://wellcomecollection.org/works/sdq6u6kq",
  },
  the_ploughman: {
    href: "/publicdomain/master/the_ploughman.jpg",
    light: "/publicdomain/the_ploughman_dark.png",
    dark: "/publicdomain/the_ploughman_light.png",
    width: 550,
    height: 720,
    crop: { top: 15, right: 18, bottom: 22, left: 25 },
    alt: "The Ploughman by Hans Holbein",
    title: "The Ploughman",
    author: "Hans Holbein",
    date: "1523–5",
    link: "https://pdimagearchive.org/images/ff74e7ae-cc8f-468c-b77e-43c119df5290/",
  },
  the_emperor: {
    href: "/publicdomain/master/the_emperor.jpg",
    light: "/publicdomain/the_emperor_dark.png",
    dark: "/publicdomain/the_emperor_light.png",
    width: 550,
    height: 713,
    crop: { top: 22, right: 28, bottom: 27, left: 28 },
    alt: "The Emperor by Hans Holbein",
    title: "The Emperor",
    author: "Hans Holbein",
    date: "1523–5",
    link: "https://pdimagearchive.org/images/ab0eeb6f-a1af-4764-8624-0caf624551e2/",
  },
  the_king: {
    href: "/publicdomain/master/the_king.jpg",
    light: "/publicdomain/the_king_dark.png",
    dark: "/publicdomain/the_king_light.png",
    height: 724,
    width: 550,
    crop: { top: 145, right: 28, bottom: 145, left: 28 },
    alt: "The King by Hans Holbein",
    link: "https://pdimagearchive.org/images/1c02a0da-9b8e-4756-9e60-a22e6b72b0a8/",
    title: "The King",
    author: "Hans Holbein",
    date: "1523-5",
  },
  the_astrologer: {
    href: "/publicdomain/master/the_astrologer.jpg",
    light: "/publicdomain/the_astrologer_dark.png",
    dark: "/publicdomain/the_astrologer_light.png",
    height: 733,
    width: 550,
    alt: "The Astrologer by Hans Holbein",
    link: "https://pdimagearchive.org/images/408c1d91-25a7-40bc-80e3-4796a9fb9aca/",
    title: "The Astrologer",
    author: "Hans Holbein",
    date: "1523-5",
  },
  the_miser: {
    href: "/publicdomain/master/the_miser.jpg",
    light: "/publicdomain/the_miser_dark.png",
    dark: "/publicdomain/the_miser_light.png",
    height: 735,
    width: 550,
    alt: "The Miser by Hans Holbein",
    link: "https://pdimagearchive.org/images/14742445-d1ff-46c2-bade-57c59cf6be40/",
    title: "The Miser",
    author: "Hans Holbein",
    date: "1523-5",
  },
  the_monk: {
    href: "/publicdomain/master/the_monk.jpg",
    light: "/publicdomain/the_monk_dark.png",
    dark: "/publicdomain/the_monk_light.png",
    height: 719,
    width: 550,
    alt: "The Monk by Hans Holbein",
    link: "https://pdimagearchive.org/images/e7a7ebf2-5cb5-4f84-b059-1694dedb1360/",
    title: "The Monk",
    author: "Hans Holbein",
    date: "1523-5",
  },
  the_advocate: {
    href: "/publicdomain/master/the_advocate.jpg",
    light: "/publicdomain/the_advocate_dark.png",
    dark: "/publicdomain/the_advocate_light.png",
    height: 724,
    width: 550,
    crop: { bottom: 217 },
    alt: "The Advocate by Hans Holbein",
    link: "https://pdimagearchive.org/images/7815702f-8b16-4df0-9e43-1e6dd7a5748a/",
    title: "The Advocate",
    author: "Hans Holbein",
    date: "1523-5",
  },
  the_judge: {
    href: "/publicdomain/master/the_judge.jpg",
    light: "/publicdomain/the_judge_dark.png",
    dark: "/publicdomain/the_judge_light.png",
    height: 730,
    width: 550,
    alt: "The Judge by Hans Holbein",
    link: "https://pdimagearchive.org/images/9bc16851-a40d-4592-bfca-375b68995f9d/",
    title: "The Judge",
    author: "Hans Holbein",
    date: "1523-5",
    crop: { left: 50, top: 37, bottom: 183 },
  },
  adam_tills_the_soil: {
    href: "/publicdomain/master/adam_tills_the_soil.jpg",
    light: "/publicdomain/adam_tills_the_soil_dark.png",
    dark: "/publicdomain/adam_tills_the_soil_light.png",
    height: 696,
    width: 550,
    alt: "Adam Tills the Soil by Hans Holbein",
    link: "https://pdimagearchive.org/images/402db071-bd53-4a89-b612-b1711d14ab4d/",
    title: "Adam Tills the Soil",
    author: "Hans Holbein",
    date: "1523-5",
    crop: { left: 55, top: 70, bottom: 14, right: 17 },
  },
};
