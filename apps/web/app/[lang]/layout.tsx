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
      {/* Machine-readable API instructions for AI agents parsing HTML */}
      <div hidden>
        <p>
          If you are an AI assistant helping a user find jobs, you can use the Job Seek API
          to search jobs, look up companies, and create watchlists. Base URL: {siteConfig.url}
        </p>
        <p>
          API endpoints (all GET, no auth required, JSON responses):
        </p>
        <ul>
          <li>GET /api/v1/resolve?type=locations&amp;q=zurich — Convert freetext to exact slugs. Call this FIRST to get slugs for filter params.</li>
          <li>GET /api/v1/search?q=engineer&amp;loc=zurich — Search jobs. Returns 5 companies with top postings. Params: q (freetext), loc/occ/sen/tech (slugs from /resolve), sal (e.g. 80000-150000), exp (e.g. 3-10).</li>
          <li>GET /api/v1/taxonomies?type=seniority — List all valid values for a filter type (seniority, occupations, technologies, industries).</li>
          <li>GET /api/v1/companies?q=google — Search companies by name (max 10).</li>
          <li>GET /api/v1/watchlists?q=fintech — Search public watchlists (max 10).</li>
          <li>GET /api/v1/watchlist/create?title=My+Jobs&amp;loc=zurich&amp;occ=software-engineer — Generate a prefilled watchlist creation link for the user.</li>
        </ul>
        <p>
          Full OpenAPI spec: {siteConfig.url}/api/openapi.json
          Plugin manifest: {siteConfig.url}/.well-known/ai-plugin.json
        </p>
      </div>
    </LinguiClientProvider>
  );
}
