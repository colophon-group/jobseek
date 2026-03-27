import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { LlmContentMirror } from "@/components/LlmContentMirror";
import { PrivacyPolicyContent } from "@/components/PrivacyPolicyContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "privacy.meta.title", message: "Privacy Policy" });
  const description = i18n._({
    id: "privacy.meta.description",
    message: "How Job Seek collects, uses, and protects your personal data.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/privacy-policy", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/privacy-policy` },
  };
}

export default async function PrivacyPolicyPage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({ id: "privacy.meta.title", message: "Privacy Policy" }),
        description: i18n._({ id: "privacy.meta.description", message: "How Job Seek collects, uses, and protects your personal data." }),
        url: `${siteConfig.url}/${locale}/privacy-policy`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
        lastReviewed: siteConfig.privacy.lastUpdated,
      }} />
      <PrivacyPolicyContent />
      <LlmContentMirror>
        <h1>{i18n._("privacy.hero.title")}</h1>
        <p>{i18n._("privacy.hero.description")}</p>
        <h2>{i18n._("privacy.short.title")}</h2>
        <ul>
          <li>{i18n._("privacy.short.r1")}</li>
          <li>{i18n._("privacy.short.r2")}</li>
          <li>{i18n._("privacy.short.r3")}</li>
          <li>{i18n._("privacy.short.r4")}</li>
          <li>{i18n._("privacy.short.r5")}</li>
        </ul>
        <h2>{i18n._("privacy.rights.title")}</h2>
        <p>{i18n._("privacy.rights.intro")}</p>
        <p>{i18n._("privacy.contact.description")} {siteConfig.indexing.contactEmail}</p>
      </LlmContentMirror>
    </>
  );
}
