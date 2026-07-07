import type { Metadata } from "next";
import type { ReactNode } from "react";
import { setI18n } from "@lingui/react/server";
import { notFound } from "next/navigation";
import { ThemeProvider } from "next-themes";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { LinguiClientProvider } from "@/components/providers/LinguiProvider";
import { LocaleGuard } from "@/components/LocaleGuard";
import { type Locale, isLocale, locales, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { JsonLd } from "@/lib/seo";
import "../globals.css";

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
};

// `[lang]/layout.tsx` is the de-facto root layout: every HTML route lives
// under `/<locale>/...`. The bare `app/layout.tsx` was removed so `<html
// lang>` can be rendered server-side per locale (closes #2826) without
// adding a `headers()` read to the root render path. Routes outside
// `[lang]/` are route handlers (sitemap, robots, /api/*, OG images) and
// don't need an HTML shell.
export const metadata: Metadata = {
  title: { template: "%s | Job Seek", default: "Job Seek" },
  metadataBase: new URL(siteConfig.url),
  twitter: {
    card: "summary_large_image",
    images: [{ url: "/opengraph-image", alt: "Job Seek" }],
  },
  // `images` here is the fallback for any page whose own
  // `generateMetadata` doesn't return its own `openGraph.images`. The
  // `/opengraph-image` URL is exempt from the locale-redirect proxy
  // (`apps/web/proxy.ts:52`), so the og:image meta tag resolves
  // directly to `app/opengraph-image.tsx` without a 308 to `/<locale>/...`.
  openGraph: {
    type: "website",
    siteName: "Job Seek",
    images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
  },
};

/** Pre-render a version of every page for each supported locale. */
export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export default async function LocaleLayout({ children, params }: Props) {
  const { lang } = await params;
  // Reject unknown locales with notFound() so requests like
  // `/<anything>.xml` (e.g. a missing `/sitemap.xml` route after
  // PR #2665) surface as 404 instead of silently rendering the
  // homepage with `[lang] = "<anything>.xml"`. See issue #2694.
  //
  // We do NOT set `dynamicParams = false` — that would inherit to
  // every nested dynamic segment (`company/[slug]`, `[userSlug]`,
  // `[userSlug]/[watchlistSlug]`) and 404 every dynamic page, since
  // those segments don't have their own `generateStaticParams`.
  if (!isLocale(lang)) notFound();
  const locale: Locale = lang;
  const { i18n, messages } = await loadCatalog(locale);
  setI18n(i18n);

  return (
    <html lang={locale} suppressHydrationWarning>
      <body>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
          <LinguiClientProvider locale={locale} messages={messages}>
            {/*
              Mounted here (root of every locale-prefixed route) so a
              browser-back to a stale-locale URL — e.g. /en/explore after
              the user picked German in /settings — auto-redirects to
              the correct /de/explore. Reads `NEXT_LOCALE` cookie on every
              pathname change. See `LocaleGuard.tsx` and #2988.
            */}
            <LocaleGuard />
            <JsonLd data={{
              "@context": "https://schema.org",
              "@type": "Organization",
              name: "Job Seek",
              url: siteConfig.url,
              logo: `${siteConfig.url}${siteConfig.logo.src}`,
              description: i18n._({
                id: "app.schema.org.description",
                comment: "Organization JSON-LD description for Job Seek and Colophon Group.",
                message: "Company-tracking tool for targeted job seekers — watchlists, email alerts, and postings sourced directly from company career pages. Built by Colophon Group, a small developer studio in Switzerland.",
              }),
              foundingDate: "2025",
              contactPoint: {
                "@type": "ContactPoint",
                email: siteConfig.indexing.contactEmail,
                contactType: "customer support",
              },
              sameAs: [siteConfig.social.linkedin.href, siteConfig.repoUrl],
            }} />
            <JsonLd data={{
              "@context": "https://schema.org",
              "@type": "WebSite",
              name: "Job Seek",
              url: siteConfig.url,
              inLanguage: locale,
              potentialAction: {
                "@type": "SearchAction",
                target: {
                  "@type": "EntryPoint",
                  urlTemplate: `${siteConfig.url}/${locale}/explore?q={search_term_string}`,
                },
                "query-input": "required name=search_term_string",
              },
            }} />
            <JsonLd data={{
              "@context": "https://schema.org",
              "@type": "WebApplication",
              name: "Job Seek",
              url: siteConfig.url,
              applicationCategory: "BusinessApplication",
              operatingSystem: "Any",
              inLanguage: locale,
              description: i18n._({
                id: "app.schema.description",
                comment: "WebApplication JSON-LD description for Job Seek.",
                message: "Company-tracking tool for job seekers who already know which companies they want to work at. Build watchlists, get email alerts when new roles open up, and track applications in one place — postings sourced directly from company career pages.",
              }),
              offers: [
                {
                  "@type": "Offer",
                  name: "Free",
                  price: "0",
                  priceCurrency: "USD",
                  description: i18n._({
                    id: "app.schema.offer.free",
                    comment: "WebApplication JSON-LD offer description for the Free plan.",
                    message: "Full search, 1 watchlist, application tracker",
                  }),
                },
                {
                  "@type": "Offer",
                  name: "Pro",
                  price: "10",
                  priceCurrency: "USD",
                  billingPeriod: "P1M",
                  description: i18n._({
                    id: "app.schema.offer.pro",
                    comment: "WebApplication JSON-LD offer description for the Pro plan.",
                    message: "Unlimited watchlists, email alerts on new matches",
                  }),
                },
              ],
              featureList: [
                i18n._({
                  id: "app.schema.feature.monitor",
                  comment: "WebApplication JSON-LD feature list item about career-page monitoring.",
                  message: "Monitor company career pages",
                }),
                i18n._({
                  id: "app.schema.feature.alerts",
                  comment: "WebApplication JSON-LD feature list item about job alert notifications.",
                  message: "Real-time job posting alerts",
                }),
                i18n._({
                  id: "app.schema.feature.languages",
                  comment: "WebApplication JSON-LD feature list item about supported interface languages.",
                  message: "Multi-language support",
                }),
                i18n._({
                  id: "app.schema.feature.watchlists",
                  comment: "WebApplication JSON-LD feature list item about watchlists and application tracking.",
                  message: "Watchlists and application tracking",
                }),
              ],
            }} />
            {children}
          </LinguiClientProvider>
        </ThemeProvider>
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
