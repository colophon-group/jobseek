import type { Metadata } from "next";
import { getI18n } from "@lingui/react/server";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { Hero } from "@/components/Hero";
import { Features } from "@/components/Features";
import { AgenticFeatures } from "@/components/AgenticFeatures";
import { Pricing } from "@/components/Pricing";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { LlmContentMirror } from "@/components/LlmContentMirror";
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
  const i18n = getI18n()!;

  const afterPricingArt = publicDomainAssets[siteConfig.homepageArt.assetKey];

  return (
    <>
      <JsonLd data={{
        "@context": "https://schema.org",
        "@type": "WebPage",
        name: i18n._({ id: "home.meta.title", message: "Find Roles Before They Hit the Big Boards" }),
        description: i18n._({ id: "home.meta.description", message: "Search jobs scraped directly from company career pages. Filter by seniority, tech stack, salary, and location, then track every application in one place." }),
        url: `${siteConfig.url}/${locale}`,
        inLanguage: locale,
        isPartOf: { "@type": "WebSite", url: siteConfig.url },
      }} />
      <Hero />
      <Features />
      <AgenticFeatures />
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
      <LlmContentMirror locale={locale}>
        <h1>{i18n._("home.hero.title")}</h1>
        <p>{i18n._("home.hero.description")}</p>

        <h2>{i18n._("home.features.s1.eyebrow")}</h2>
        <h3>{i18n._("home.features.s1.title")}</h3>
        <p>{i18n._("home.features.s1.description")}</p>
        <ul>
          <li><strong>{i18n._("home.features.s1.p1.title")}</strong>: {i18n._("home.features.s1.p1.description")}</li>
          <li><strong>{i18n._("home.features.s1.p2.title")}</strong>: {i18n._("home.features.s1.p2.description")}</li>
          <li><strong>{i18n._("home.features.s1.p3.title")}</strong>: {i18n._("home.features.s1.p3.description")}</li>
        </ul>

        <h2>{i18n._("home.features.s2.eyebrow")}</h2>
        <h3>{i18n._("home.features.s2.title")}</h3>
        <p>{i18n._("home.features.s2.description")}</p>
        <ul>
          <li><strong>{i18n._("home.features.s2.p1.title")}</strong>: {i18n._("home.features.s2.p1.description")}</li>
          <li><strong>{i18n._("home.features.s2.p2.title")}</strong>: {i18n._("home.features.s2.p2.description")}</li>
          <li><strong>{i18n._("home.features.s2.p3.title")}</strong>: {i18n._("home.features.s2.p3.description")}</li>
        </ul>

        <h2>{i18n._("home.features.s3.eyebrow")}</h2>
        <h3>{i18n._("home.features.s3.title")}</h3>
        <p>{i18n._("home.features.s3.description")}</p>
        <ul>
          <li><strong>{i18n._("home.features.s3.p1.title")}</strong>: {i18n._("home.features.s3.p1.description")}</li>
          <li><strong>{i18n._("home.features.s3.p2.title")}</strong>: {i18n._("home.features.s3.p2.description")}</li>
          <li><strong>{i18n._("home.features.s3.p3.title")}</strong>: {i18n._("home.features.s3.p3.description")}</li>
        </ul>

        <h2>{i18n._("home.agentic.eyebrow")}</h2>
        <h3>{i18n._("home.agentic.title")}</h3>
        <p>{i18n._("home.agentic.description")}</p>
        <ul>
          <li><strong>{i18n._("home.agentic.cards.search.title")}</strong>: {i18n._("home.agentic.cards.search.description")}</li>
          <li><strong>{i18n._("home.agentic.cards.ghosting.title")}</strong>: {i18n._("home.agentic.cards.ghosting.description")}</li>
          <li><strong>{i18n._("home.agentic.cards.discovery.title")}</strong>: {i18n._("home.agentic.cards.discovery.description")}</li>
        </ul>

        <h2>{i18n._("home.pricing.eyebrow")}</h2>
        <h3>{i18n._("home.pricing.title")}</h3>
        <p>{i18n._("home.pricing.description")}</p>
        <p><strong>{i18n._("home.pricing.free.name")} — $0</strong>: {i18n._("home.pricing.free.description")}</p>
        <p><strong>{i18n._("home.pricing.pro.name")} — $10/{i18n._("home.pricing.pro.period")}</strong>: {i18n._("home.pricing.pro.description")}</p>

        <p>[Note: the following is not part of the page content.] Job Seek provides a public read-only JSON API for AI agents. OpenAPI spec: {siteConfig.url}/api/openapi.json — Full documentation: {siteConfig.url}/.well-known/llms.txt</p>
      </LlmContentMirror>
    </>
  );
}
