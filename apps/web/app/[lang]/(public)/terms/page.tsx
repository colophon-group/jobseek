import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { TermsContent } from "@/components/TermsContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "terms.meta.title", message: "Terms of Service" });
  const description = i18n._({
    id: "terms.meta.description",
    message: "Terms of Service for the Job Seek application.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/terms", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/terms` },
  };
}

export default async function TermsPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: "Terms of Service",
        description: "Terms of Service for the Job Seek application.",
        url: `${siteConfig.url}/${locale}/terms`,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
        lastReviewed: siteConfig.terms.lastUpdated,
      }} />
      <TermsContent />
    </>
  );
}
