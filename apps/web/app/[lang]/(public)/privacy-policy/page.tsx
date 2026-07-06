import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
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
    message: "Job Seek privacy policy — what personal data we collect, how we use it, your GDPR rights, cookie policy, and how to request deletion of your account and data.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/privacy-policy", locale),
    // Excluded from the index (#2822): footer link satisfies legal
    // accessibility; nobody discovers the page via search. `follow`
    // keeps PageRank flowing.
    robots: { index: false, follow: true },
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/privacy-policy`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
    },
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
        description: i18n._({ id: "privacy.meta.description", message: "Job Seek privacy policy — what personal data we collect, how we use it, your GDPR rights, cookie policy, and how to request deletion of your account and data." }),
        url: `${siteConfig.url}/${locale}/privacy-policy`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
        lastReviewed: siteConfig.privacy.lastUpdated,
      }} />
      <PrivacyPolicyContent />
    </>
  );
}
