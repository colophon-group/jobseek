import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
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
    message: "How Job Seek discovers, crawls, and indexes job postings — and the safeguards we follow.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/how-we-index", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/how-we-index` },
  };
}

export default async function HowWeIndexPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: "How We Index",
        description: "How Job Seek discovers, crawls, and indexes job postings — and the safeguards we follow.",
        url: `${siteConfig.url}/${locale}/how-we-index`,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <HowWeIndexContent />
    </>
  );
}
