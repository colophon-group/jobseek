import { setI18n } from "@lingui/react/server";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { type Locale, isLocale, locales, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { JsonLd } from "@/lib/seo";

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
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
    <LinguiClientProvider locale={locale} messages={messages}>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "Organization",
        name: "Job Seek",
        url: siteConfig.url,
        logo: `${siteConfig.url}${siteConfig.logo.src}`,
        description: i18n._({
          id: "app.schema.org.description",
          message: "Job search engine that scrapes career pages directly from company websites. Built by Colophon Group in Switzerland.",
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
          message: "Job aggregator that monitors company career pages. Subscribe to company updates, track applications, and get alerts on new openings.",
        }),
        offers: [
          {
            "@type": "Offer",
            name: "Free",
            price: "0",
            priceCurrency: "USD",
            description: i18n._({ id: "app.schema.offer.free", message: "Full search, 1 watchlist, application tracker" }),
          },
          {
            "@type": "Offer",
            name: "Pro",
            price: "10",
            priceCurrency: "USD",
            billingPeriod: "P1M",
            description: i18n._({ id: "app.schema.offer.pro", message: "Unlimited watchlists, email alerts on new matches" }),
          },
        ],
        featureList: [
          i18n._({ id: "app.schema.feature.monitor", message: "Monitor company career pages" }),
          i18n._({ id: "app.schema.feature.alerts", message: "Real-time job posting alerts" }),
          i18n._({ id: "app.schema.feature.languages", message: "Multi-language support" }),
          i18n._({ id: "app.schema.feature.watchlists", message: "Watchlists and application tracking" }),
        ],
      }} />
      {children}
    </LinguiClientProvider>
  );
}
