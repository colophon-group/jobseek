import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { Hero } from "@/components/Hero";
import { Features } from "@/components/Features";
import { Pricing } from "@/components/Pricing";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { buildAlternates, JsonLd } from "@/lib/seo";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({
    id: "home.meta.title",
    comment: "SEO title for the public homepage.",
    message: "Track the companies you want to work at — Job Seek",
  });
  const description = i18n._({
    id: "home.meta.description",
    comment: "SEO description for the public homepage.",
    message: "Build watchlists of the companies you care about, review matching roles, and track applications in one place. Postings come direct from company career pages, within hours of going live — no recruiter spam, no reposted listings.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}`,
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ ...siteConfig.ogImage, alt: "Job Seek" }],
    },
  };
}

export default async function HomePage({ params }: Props) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;

  const afterPricingArt = publicDomainAssets[siteConfig.homepageArt.assetKey];

  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({
          id: "home.meta.title",
          comment: "JSON-LD page name for the public homepage.",
          message: "Track the companies you want to work at — Job Seek",
        }),
        description: i18n._({
          id: "home.meta.description",
          comment: "JSON-LD page description for the public homepage.",
          message: "Build watchlists of the companies you care about, review matching roles, and track applications in one place. Postings come direct from company career pages, within hours of going live — no recruiter spam, no reposted listings.",
        }),
        url: `${siteConfig.url}/${locale}`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <main id="main-content" tabIndex={-1} className="scroll-mt-12" suppressHydrationWarning>
        <Hero />
        <Features />
        <Pricing />
        {afterPricingArt && (
          <section className="py-20">
            <div className="mx-auto max-w-[1200px] px-4">
              <div className="mx-auto h-[360px] w-full max-w-[768px] sm:h-[460px] lg:h-[560px]">
                <PublicDomainArt
                  asset={afterPricingArt}
                  focus={siteConfig.homepageArt.focus}
                  sizes="(min-width: 768px) 768px, 100vw"
                  className="h-full w-full"
                />
              </div>
            </div>
          </section>
        )}
      </main>
    </>
  );
}
