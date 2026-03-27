import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { LlmContentMirror } from "@/components/LlmContentMirror";
import { LicenseContent } from "@/components/LicenseContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "license.meta.title", message: "License" });
  const description = i18n._({
    id: "license.meta.description",
    message: "Licensing terms for Job Seek application code (MIT) and job data (CC BY-NC 4.0).",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/license", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/license` },
  };
}

export default async function LicensePage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({ id: "license.meta.title", message: "License" }),
        description: i18n._({ id: "license.meta.description", message: "Licensing terms for Job Seek application code (MIT) and job data (CC BY-NC 4.0)." }),
        url: `${siteConfig.url}/${locale}/license`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <LicenseContent />
      <LlmContentMirror>
        <h1>{i18n._("license.hero.title")}</h1>
        <p>{i18n._("license.hero.description")}</p>
        <h2>{i18n._("license.code.title")}</h2>
        <p>{i18n._("license.code.summary")}</p>
        <h2>{i18n._("license.data.title")}</h2>
        <p>{i18n._("license.data.summary")}</p>
        <h2>{i18n._("license.contact.title")}</h2>
        <p>{i18n._("license.contactCta")} {siteConfig.indexing.contactEmail}</p>
      </LlmContentMirror>
    </>
  );
}
