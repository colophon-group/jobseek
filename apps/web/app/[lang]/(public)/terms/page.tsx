import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { LlmContentMirror } from "@/components/LlmContentMirror";
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
  const i18n = getI18n()!;
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({ id: "terms.meta.title", message: "Terms of Service" }),
        description: i18n._({ id: "terms.meta.description", message: "Terms of Service for the Job Seek application." }),
        url: `${siteConfig.url}/${locale}/terms`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
        lastReviewed: siteConfig.terms.lastUpdated,
      }} />
      <TermsContent />
      <LlmContentMirror locale={locale}>
        <h1>{i18n._("terms.hero.title")}</h1>
        <p>{i18n._("terms.hero.description")}</p>
        <h2>{i18n._("terms.short.title")}</h2>
        <ul>
          <li>{i18n._("terms.short.r1")}</li>
          <li>{i18n._("terms.short.r2")}</li>
          <li>{i18n._("terms.short.r3")}</li>
          <li>{i18n._("terms.short.r4")}</li>
          <li>{i18n._("terms.short.r5")}</li>
        </ul>
        <p>{i18n._("terms.contact.description")} {siteConfig.indexing.contactEmail}</p>
      </LlmContentMirror>
    </>
  );
}
