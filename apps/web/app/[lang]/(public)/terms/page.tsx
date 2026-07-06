import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
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

  const title = i18n._({
    id: "terms.meta.title",
    comment: "SEO title for the public Terms of Service page.",
    message: "Terms of Service",
  });
  const description = i18n._({
    id: "terms.meta.description",
    comment: "SEO description for the public Terms of Service page.",
    message: "Terms of Service for Job Seek — usage rules, intellectual property, account responsibilities, limitation of liability, and governing law for the job search platform.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/terms", locale),
    // Excluded from the index (#2822): footer link satisfies legal
    // accessibility; nobody discovers the page via search. `follow`
    // keeps PageRank flowing.
    robots: { index: false, follow: true },
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/terms`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
    },
  };
}

export default async function TermsPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({
          id: "terms.meta.title",
          comment: "JSON-LD page name for the public Terms of Service page.",
          message: "Terms of Service",
        }),
        description: i18n._({
          id: "terms.meta.description",
          comment: "JSON-LD page description for the public Terms of Service page.",
          message: "Terms of Service for Job Seek — usage rules, intellectual property, account responsibilities, limitation of liability, and governing law for the job search platform.",
        }),
        url: `${siteConfig.url}/${locale}/terms`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
        lastReviewed: siteConfig.terms.lastUpdated,
      }} />
      <TermsContent />
    </>
  );
}
