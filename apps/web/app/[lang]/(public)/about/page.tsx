import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { LlmContentMirror } from "@/components/LlmContentMirror";
import { AboutContent } from "./about-content";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "about.meta.title", message: "About" });
  const description = i18n._({
    id: "about.meta.description",
    message: "Job Seek helps you track the companies you want to work at — watchlists, email alerts, and postings sourced directly from company career pages. Built by Colophon Group, a small developer studio in Switzerland.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/about", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/about` },
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
        name: i18n._({ id: "about.meta.title", message: "About" }),
        description: i18n._({ id: "about.meta.description", message: "Job Seek helps you track the companies you want to work at — watchlists, email alerts, and postings sourced directly from company career pages. Built by Colophon Group, a small developer studio in Switzerland." }),
        url: `${siteConfig.url}/${locale}/about`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <AboutContent
        contactEmail={siteConfig.indexing.contactEmail}
        ossRepoUrl={siteConfig.indexing.ossRepoUrl}
      />
      <LlmContentMirror locale={locale}>
        <h1>{i18n._("about.title")}</h1>
        <p>{i18n._("about.p1")}</p>
        <p>{i18n._("about.p2")}</p>
        <p>{i18n._("about.p3")}</p>
        <h2>{i18n._("about.transparency.title")}</h2>
        <p>{i18n._("about.transparency.p1")}</p>
        <p>Contact: {siteConfig.indexing.contactEmail}</p>
      </LlmContentMirror>
    </>
  );
}
