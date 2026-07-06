import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { AboutContent } from "./about-content";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({
    id: "about.meta.title",
    comment: "SEO title for the public About page.",
    message: "About",
  });
  const description = i18n._({
    id: "about.meta.description",
    comment: "SEO description for the public About page.",
    message: "Job Seek helps you track the companies you want to work at — watchlists, email alerts, and postings sourced directly from company career pages. Built by Colophon Group, a small developer studio in Switzerland.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/about", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/about`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
    },
  };
}

export default async function AboutPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({
          id: "about.meta.title",
          comment: "JSON-LD page name for the public About page.",
          message: "About",
        }),
        description: i18n._({
          id: "about.meta.description",
          comment: "JSON-LD page description for the public About page.",
          message: "Job Seek helps you track the companies you want to work at — watchlists, email alerts, and postings sourced directly from company career pages. Built by Colophon Group, a small developer studio in Switzerland.",
        }),
        url: `${siteConfig.url}/${locale}/about`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <AboutContent
        contactEmail={siteConfig.indexing.contactEmail}
        ossRepoUrl={siteConfig.indexing.ossRepoUrl}
      />
    </>
  );
}
