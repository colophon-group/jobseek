import { setI18n } from "@lingui/react/server";
import type { ReactNode } from "react";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { type Locale, isLocale, defaultLocale, locales, loadCatalog } from "@/lib/i18n";
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
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;
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
        offers: {
          "@type": "Offer",
          price: "0",
          priceCurrency: "USD",
        },
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
