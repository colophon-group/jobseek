import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { HowWeIndexContent } from "@/components/HowWeIndexContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "indexing.meta.title", message: "How We Index" });
  const description = i18n._({
    id: "indexing.meta.description",
    message: "How Job Seek discovers and indexes job postings: our sourcing approach, crawl frequency, rate limits, robots.txt and TDM-Reservation compliance, and how to opt out.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/how-we-index", locale),
    // Excluded from the index (#2822): policy/methodology page reached
    // from the footer; not a search-discovery surface. `follow` keeps
    // PageRank flowing.
    robots: { index: false, follow: true },
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/how-we-index`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
    },
  };
}

export default async function HowWeIndexPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({ id: "indexing.meta.title", message: "How We Index" }),
        description: i18n._({ id: "indexing.meta.description", message: "How Job Seek discovers and indexes job postings: our sourcing approach, crawl frequency, rate limits, robots.txt and TDM-Reservation compliance, and how to opt out." }),
        url: `${siteConfig.url}/${locale}/how-we-index`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <HowWeIndexContent />
    </>
  );
}
