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
  url: "https://jobseek.com",
  domain: "jobseek.com",
  repoUrl: "https://github.com/colophon-group/jobseek-frontend",
  creator: "Viktor Shcherbakov",

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

  nav: {
    product: { href: "/" },
    features: { href: "/#features" },
    pricing: { href: "/#pricing" },
    company: { href: "/how-we-index" },
    license: { href: "/license", hidden: true },
    login: { href: "/handler/sign-up", hidden: true },
    dashboard: { href: "/dashboard", hidden: true },
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
          light: "/js_missing_screenshot_black.png",
          dark: "/js_missing_screenshot_white.png",
          width: 1200,
          height: 630,
        },
        pointIcons: ["notifications", "check_circle", "bookmark"] as const,
      },
      {
        screenshot: {
          light: "/js_missing_screenshot_black.png",
          dark: "/js_missing_screenshot_white.png",
          width: 1200,
          height: 630,
        },
        pointIcons: ["bug", "travel_explore", "campaign"] as const,
      },
    ],
  },

  pricing: {
    anchorId: "pricing",
    free: { href: "/handler/sign-up", highlight: false },
    pro: { href: "/handler/sign-up", highlight: true },
  },

  indexing: {
    botName: "JobSeekBot",
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

  homepageArt: {
    assetKey: "the_miser" as const,
    focus: { x: 45, y: 40 },
  },

  seo: {
    disallow: ["/dashboard", "/handler/"],
    sitemap: [
      { path: "/", changeFrequency: "weekly", priority: 1 },
      { path: "/how-we-index", changeFrequency: "monthly", priority: 0.6 },
      { path: "/license", changeFrequency: "monthly", priority: 0.5 },
    ],
  },

  footer: {
    links: [
      { href: "https://github.com/colophon-group/jobseek-frontend", external: true },
      { href: "mailto:business@colophon-group.org", external: true },
      { href: "/license", external: false },
    ],
  },

  ui: {
    externalLinkTracking: {
      utmSource: "jobseek",
      utmMedium: "website",
    },
  },
} as const;

export const publicDomainAssets: Record<string, PublicDomainAsset> = {
  operateur_cephalique: {
    href: "/publicdomain/master/operateur_cephalique.jpg",
    light: "/publicdomain/operateur_cephalique_dark.png",
    dark: "/publicdomain/operateur_cephalique_light.png",
    height: 1024,
    width: 774,
    alt: "Operateur Cephalique by Campion",
    link: "https://pdimagearchive.org/images/d526ff08-72e3-4ebf-a858-ffc50becbd56",
    title: "Operateur Cephalique",
    author: "Campion",
    date: "1663",
  },
  the_king: {
    href: "/publicdomain/master/the_king.jpg",
    light: "/publicdomain/the_king_dark.png",
    dark: "/publicdomain/the_king_light.png",
    height: 724,
    width: 550,
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
    crop: { left: 50, bottom: 120 },
  },
  expulsion_from_paradise: {
    href: "/publicdomain/master/expulsion_from_paradise.jpg",
    light: "/publicdomain/expulsion_from_paradise_dark.png",
    dark: "/publicdomain/expulsion_from_paradise_light.png",
    height: 705,
    width: 550,
    alt: "Expulsion from Paradise by Hans Holbein",
    link: "https://pdimagearchive.org/images/402db071-bd53-4a89-b612-b1711d14ab4d/",
    title: "Expulsion from Paradise",
    author: "Hans Holbein",
    date: "1523-5",
  },
};
