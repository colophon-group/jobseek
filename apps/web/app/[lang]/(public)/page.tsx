import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { Hero } from "@/components/Hero";
import { Features } from "@/components/Features";
import { Pricing } from "@/components/Pricing";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { siteConfig, publicDomainAssets } from "@/content/config";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "home.meta.title", message: "Find Relevant Roles Faster" });
  const description = i18n.t({
    id: "home.meta.description",
    message: "Subscribe to updates from companies, track applications, and never miss new openings.",
  });

  return {
    title,
    description,
    openGraph: { title, description, url: `/${locale}` },
  };
}

export default async function HomePage({ params }: Props) {
  await initI18nForPage(params);

  const afterPricingArt = publicDomainAssets[siteConfig.homepageArt.assetKey];

  return (
    <>
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
