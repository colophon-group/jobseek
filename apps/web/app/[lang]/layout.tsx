import { setI18n } from "@lingui/react/server";
import type { ReactNode } from "react";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { type Locale, isLocale, defaultLocale, locales, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { JsonLd } from "@/lib/seo";

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
};

/** Pre-render a version of every page for each supported locale. */
export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export default async function LocaleLayout({ children, params }: Props) {
  const { lang } = await params;
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n, messages } = await loadCatalog(locale);
  setI18n(i18n);

  return (
    <LinguiClientProvider locale={locale} messages={messages}>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "Organization",
        name: "Job Seek",
        url: siteConfig.url,
        logo: `${siteConfig.url}${siteConfig.logo.src}`,
        sameAs: [siteConfig.social.linkedin.href],
      }} />
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebSite",
        name: "Job Seek",
        url: siteConfig.url,
        potentialAction: {
          "@type": "SearchAction",
          target: {
            "@type": "EntryPoint",
            urlTemplate: `${siteConfig.url}/en/explore?q={search_term_string}`,
          },
          "query-input": "required name=search_term_string",
        },
      }} />
      {children}
    </LinguiClientProvider>
  );
}
