import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";
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
    message: "Licensing for Job Seek — application source code is MIT-licensed, job posting data is CC BY-NC 4.0. Learn what you can and cannot do with our code and data.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/license", locale),
    // Excluded from the index (#2822): nobody Googles "jobseek license";
    // the page is reachable from the footer of every page, which is
    // sufficient for legal accessibility. Indexing it dilutes the
    // surface and wastes crawl budget. `follow` keeps PageRank flowing.
    robots: { index: false, follow: true },
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/license`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
    },
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
        description: i18n._({ id: "license.meta.description", message: "Licensing for Job Seek — application source code is MIT-licensed, job posting data is CC BY-NC 4.0. Learn what you can and cannot do with our code and data." }),
        url: `${siteConfig.url}/${locale}/license`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <LicenseContent />
    </>
  );
}
