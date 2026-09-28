import type { Metadata } from "next";
import Link from "next/link";
import { getI18n } from "@lingui/react/server";
import { ArrowRight } from "lucide-react";
import { narrowedMessages as copy } from "@/content/narrowed";
import { siteConfig } from "@/content/config";
import { Button } from "@/components/ui/Button";
import { Pricing } from "@/components/Pricing";
import { MarketingLinks, type MarketingPageProps } from "@/components/marketing/MarketingPage";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { paddleCheckoutEnabled } from "@/lib/paddle/config";

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
  const steps = [[copy.step1Title, copy.step1Body], [copy.step2Title, copy.step2Body], [copy.step3Title, copy.step3Body]];
  return <>
    <JsonLd data={{ "@context": "https://schema.org", "@type": "WebPage", name: i18n._(copy.metaTitle), description: i18n._(copy.description), url: `${siteConfig.url}/${locale}/narrowed`, inLanguage: locale }} />
    <main id="main-content" tabIndex={-1} className="scroll-mt-12">
      <section className="mx-auto max-w-[1200px] px-4 pb-4 pt-12 md:pt-20">
        <div className="max-w-3xl">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted">Job Seek Pro / Narrowed</p>
          <h1 className="mt-4 text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{i18n._(copy.title)}</h1>
          <p className="mt-6 max-w-2xl leading-7 text-muted">{i18n._(copy.body)}</p>
          <div className="mt-7 flex flex-wrap items-center gap-5">
            <Button href={`/${locale}/explore`} className="max-w-full gap-2 whitespace-normal! text-center">{i18n._(copy.cta)}<ArrowRight size={16} className="shrink-0" aria-hidden="true" /></Button>
            <Link href={`/${locale}/settings/billing`} prefetch={false} className="text-sm underline underline-offset-4">{i18n._(copy.pricing)}</Link>
          </div>
          <p className="mt-3 text-xs leading-6 text-muted">{i18n._(copy.note)}</p>
        </div>
      </section>
      <section className="mx-auto max-w-[1200px] px-4 py-12 md:py-16">
        <h2 className="text-2xl font-semibold tracking-tight">{i18n._(copy.proofTitle)}</h2>
        <p className="mt-4 max-w-3xl text-sm leading-7 text-muted">{i18n._(copy.proofBody)}</p>
        <div className="mt-6 max-w-3xl">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{i18n._(copy.examplesTitle)}</h3>
          <blockquote className="mt-3 border-l-2 border-foreground pl-5 text-sm leading-7">{i18n._(copy.example1)}</blockquote>
        </div>
        <p className="mt-5 text-xs leading-6 text-muted">{i18n._(copy.proofCaption)}</p>
      </section>
      <section className="mx-auto max-w-[1200px] px-4">
        <div className="border-t border-divider pt-10">
          <h2 className="text-2xl font-semibold tracking-tight">{i18n._(copy.stepsTitle)}</h2>
          <ol className="mt-8 grid gap-8 md:grid-cols-3">
            {steps.map(([title, body], index) => <li key={title.id}><span className="text-xs text-muted" aria-hidden="true">0{index + 1}</span><h3 className="mt-3 text-lg font-semibold">{i18n._(title)}</h3><p className="mt-3 text-sm leading-7 text-muted">{i18n._(body)}</p></li>)}
          </ol>
        </div>
      </section>
      <Pricing checkoutEnabled={paddleCheckoutEnabled()} showLearnMore={false} showPitch={false} />
      <div className="mx-auto max-w-[1200px] px-4 pb-16"><MarketingLinks locale={locale} /></div>
    </main>
  </>;
}
