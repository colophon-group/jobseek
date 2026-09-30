import type { Metadata } from "next";
import { ArticleJumpLink } from "@/components/blog/ArticleJumpLink";
import { getI18n } from "@lingui/react/server";
import { ArrowRight } from "lucide-react";
import { narrowedMessages as copy } from "@/content/narrowed";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { Button } from "@/components/ui/Button";
import { Pricing } from "@/components/Pricing";
import { ProPitch } from "@/components/pro/ProPitch";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { MarketingLinks, type MarketingPageProps } from "@/components/marketing/MarketingPage";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { stripeCheckoutEnabled } from "@/lib/stripe/config";

export async function generateMetadata({ params }: MarketingPageProps): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);
  const title = i18n._(copy.metaTitle);
  const description = i18n._(copy.description);
  return {
    title, description, alternates: buildAlternates("/narrowed", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/narrowed`, locale: ogLocale(locale), alternateLocale: ogAlternateLocales(locale), images: [{ ...siteConfig.ogImage, alt: "Job Seek" }] },
  };
}

export default async function Page({ params }: MarketingPageProps) {
  const locale = await initI18nForPage(params);
  const i18n = getI18n()!;
  const steps = [
    [copy.step1Title, copy.step1Body],
    [copy.step2Title, copy.step2Body],
    [copy.step3Title, copy.step3Body],
  ];

  return <>
    <JsonLd data={{ "@context": "https://schema.org", "@type": "WebPage", name: i18n._(copy.metaTitle), description: i18n._(copy.description), url: `${siteConfig.url}/${locale}/narrowed`, inLanguage: locale }} />
    <main id="main-content" tabIndex={-1} className="scroll-mt-12">
      <section className="mx-auto grid max-w-[1200px] items-center gap-7 px-4 pb-6 pt-9 md:grid-cols-[1.15fr_1fr] md:gap-12 md:pb-8 md:pt-12">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted">Job Seek Pro / Narrowed</p>
          <h1 className="mt-4 max-w-xl text-balance break-words text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{i18n._(copy.title)}</h1>
          <p className="mt-4 max-w-xl leading-7 text-muted">{i18n._(copy.body)}</p>
          <div className="mt-5 flex flex-wrap items-center gap-5">
            <Button href={`/${locale}/explore`} className="max-w-full gap-2 whitespace-normal! text-center">
              {i18n._(copy.cta)}<ArrowRight size={16} className="shrink-0" aria-hidden="true" />
            </Button>
            <span className="text-sm underline underline-offset-4"><ArticleJumpLink href={`#${siteConfig.pricing.anchorId}`}>{i18n._(copy.pricing)}</ArticleJumpLink></span>
          </div>
          <p className="mt-3 text-xs leading-6 text-muted">{i18n._(copy.note)}</p>
        </div>
        <div className="mx-auto h-[180px] w-full max-w-[420px] sm:h-[260px] md:h-[300px] lg:ml-auto lg:mr-0">
          <PublicDomainArt
            asset={publicDomainAssets.the_woodcutter}
            loading="eager"
            fetchPriority="high"
            themeRendering="css-invert"
            sizes="(min-width: 768px) 420px, 100vw"
            className="h-full w-full"
          />
        </div>
      </section>

      <section className="mx-auto grid max-w-[1200px] items-start gap-5 px-4 py-6 lg:grid-cols-[0.8fr_1.2fr] lg:gap-x-12 lg:gap-y-6">
        <div>
          <h2 className="max-w-sm text-2xl font-semibold tracking-tight sm:text-3xl">{i18n._(copy.exampleTitle)}</h2>
          <p className="mt-4 max-w-md text-sm leading-7 text-muted">{i18n._(copy.exampleBody)}</p>
        </div>
        <div className="min-w-0 lg:col-start-2 lg:row-span-2">
          <ProPitch previewOnly />
          <aside className="mt-5 border-l-2 border-border-soft pl-4">
            <h3 className="text-xs font-semibold text-muted">{i18n._(copy.excludedTitle)}</h3>
            <a href="https://careers.datadoghq.com/detail/8007606/?gh_jid=8007606" target="_blank" rel="noopener noreferrer" className="mt-2 block text-sm font-medium underline underline-offset-4">Datadog · Staff Software Engineer – Security Agent</a>
            <p className="mt-2 text-sm leading-6 text-muted">{i18n._(copy.excludedReason)}</p>
          </aside>
        </div>
        <div className="border-t border-divider pt-5 lg:col-start-1">
          <h2 className="text-lg font-semibold tracking-tight">{i18n._(copy.stepsTitle)}</h2>
          <ol className="mt-4 grid gap-4">
            {steps.map(([title, body], index) => <li key={title.id}>
              <h3 className="flex items-baseline gap-3 text-base font-semibold"><span className="text-xs font-normal text-muted" aria-hidden="true">0{index + 1}</span>{i18n._(title)}</h3>
              <p className="mt-2 text-sm leading-6 text-muted">{i18n._(body)}</p>
            </li>)}
          </ol>
        </div>
      </section>
      <Pricing checkoutEnabled={stripeCheckoutEnabled()} showLearnMore={false} showPitch={false} compact />
      <div className="mx-auto max-w-[1200px] px-4 pb-8"><MarketingLinks locale={locale} /></div>
    </main>
  </>;
}
