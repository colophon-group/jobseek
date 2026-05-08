import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { LlmContentMirror } from "@/components/LlmContentMirror";
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
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/how-we-index` },
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
      <LlmContentMirror locale={locale}>
        <h1>{i18n._("indexing.hero.title")}</h1>
        <p>{i18n._("indexing.hero.description")}</p>

        <h2>{i18n._("indexing.assurances.title")}</h2>
        <ul>
          <li><strong>{i18n._("indexing.assurances.i1.title")}</strong> {i18n._("indexing.assurances.i1.body")}</li>
          <li><strong>{i18n._("indexing.assurances.i2.title")}</strong> {i18n._("indexing.assurances.i2.body")}</li>
          <li><strong>{i18n._("indexing.assurances.i3.title")}</strong> {i18n._("indexing.assurances.i3.body")}</li>
        </ul>

        <h2>{i18n._("indexing.ingestion.title")}</h2>
        <ol>
          <li><strong>{i18n._("indexing.ingestion.s1.title")}</strong> {i18n._("indexing.ingestion.s1.body")}</li>
          <li><strong>{i18n._("indexing.ingestion.s2.title")}</strong> {i18n._("indexing.ingestion.s2.body")}</li>
          <li><strong>{i18n._("indexing.ingestion.s3.title")}</strong> {i18n._("indexing.ingestion.s3.body")}</li>
          <li><strong>{i18n._("indexing.ingestion.s4.title")}</strong> {i18n._("indexing.ingestion.s4.body")}</li>
        </ol>

        <h2>{i18n._("indexing.optOut.title")}</h2>
        <p>{i18n._("indexing.optOut.body")} {siteConfig.indexing.contactEmail}</p>

        <h2>{i18n._("indexing.automation.title")}</h2>
        <p>{i18n._("indexing.automation.body")}</p>

        <h2>{i18n._("indexing.oss.title")}</h2>
        <p>{i18n._("indexing.oss.body")} {siteConfig.indexing.ossRepoUrl}</p>
      </LlmContentMirror>
    </>
  );
}
