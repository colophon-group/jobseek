import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
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

  const title = i18n._({ id: "home.meta.title", message: "Find Roles Before They Hit the Big Boards" });
  const description = i18n._({
    id: "home.meta.description",
    message: "Search jobs scraped directly from company career pages. Filter by seniority, tech stack, salary, and location, then track every application in one place.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}` },
  };
}

export default async function HomePage({ params }: Props) {
  const locale = await initI18nForPage(params);

  const afterPricingArt = publicDomainAssets[siteConfig.homepageArt.assetKey];

  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: getI18n()!._({ id: "home.meta.title", message: "Find Roles Before They Hit the Big Boards" }),
        description: getI18n()!._({ id: "home.meta.description", message: "Search jobs scraped directly from company career pages. Filter by seniority, tech stack, salary, and location, then track every application in one place." }),
        url: `${siteConfig.url}/${locale}`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
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
                priority
                className="h-full w-full"
              />
            </div>
          </div>
        </section>
      )}
    </>
  );
}
