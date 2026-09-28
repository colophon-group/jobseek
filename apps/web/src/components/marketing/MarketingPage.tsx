import type { Metadata } from "next";
import Link from "next/link";
import { getI18n } from "@lingui/react/server";
import { ArrowRight, Bell, Bookmark, Check, Mail } from "lucide-react";
import { marketingMessages as copy } from "@/content/marketing";
import { narrowedMessages } from "@/content/narrowed";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { Button } from "@/components/ui/Button";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { ThemedImage } from "@/components/ThemedImage";
import { buildAlternates, JsonLd } from "@/lib/seo";
import { defaultLocale, isLocale, loadCatalog, ogAlternateLocales, ogLocale, type Locale } from "@/lib/i18n";

export type MarketingKind = "alerts" | "tracker";
export type MarketingPageProps = { params: Promise<{ lang: string }> };
const paths = { alerts: "/job-alerts", tracker: "/job-application-tracker" };

export async function marketingMetadata(kind: MarketingKind, params: MarketingPageProps["params"]): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);
  const title = i18n._(kind === "tracker" ? copy.tracker_eyebrow : copy.alerts_title);
  const description = i18n._(copy[`${kind}_description`]);
  return {
    title, description, alternates: buildAlternates(paths[kind], locale),
    openGraph: {
      title, description, url: `${siteConfig.url}/${locale}${paths[kind]}`,
      locale: ogLocale(locale), alternateLocale: ogAlternateLocales(locale),
      images: [{ ...siteConfig.ogImage, alt: "Job Seek" }],
    },
  };
}

export function MarketingLinks({ locale }: { locale: Locale }) {
  const i18n = getI18n()!;
  return (
    <nav aria-label={i18n._(copy.common_related)} className="flex flex-wrap gap-x-6 gap-y-3 text-sm">
      {([
        ["/job-alerts", copy.alerts_link],
        ["/job-application-tracker", copy.tracker_link],
        ["/narrowed", narrowedMessages.link],
        ["/blog/job-alerts-from-company-career-pages", copy.guide_link],
      ] as const).map(([path, label]) => <Link key={path} href={`/${locale}${path}`} prefetch={false} className="underline underline-offset-4 hover:text-muted">{i18n._(label)}</Link>)}
    </nav>
  );
}

export function MarketingPage({ kind, locale }: { kind: MarketingKind; locale: Locale }) {
  const i18n = getI18n()!;
  const alerts = kind === "alerts";
  const screenshot = siteConfig.features.sections[alerts ? 2 : 1].screenshot;
  const steps = [
    [copy[`${kind}_step1Title`], copy[`${kind}_step1Body`]],
    [copy[`${kind}_step2Title`], copy[`${kind}_step2Body`]],
    [copy[`${kind}_step3Title`], copy[`${kind}_step3Body`]],
  ];
  return (
    <>
      <JsonLd data={{ "@context": "https://schema.org", "@type": "WebPage", name: i18n._(copy[`${kind}_title`]), description: i18n._(copy[`${kind}_description`]), url: `${siteConfig.url}/${locale}${paths[kind]}`, inLanguage: locale }} />
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-[1200px] scroll-mt-12 px-4 py-12 md:py-20">
        <section className="grid items-center gap-10 md:grid-cols-[1.15fr_1fr] lg:gap-16">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">{i18n._(copy[`${kind}_eyebrow`])}</p>
            <h1 className="mt-4 max-w-2xl break-words text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{i18n._(copy[`${kind}_title`])}</h1>
            <p className="mt-6 max-w-xl leading-7 text-muted">{i18n._(copy[`${kind}_body`])}</p>
            <Button href={`/${locale}/explore`} className="mt-7 max-w-full gap-2 whitespace-normal! text-center">{i18n._(copy[`${kind}_cta`])}<ArrowRight size={16} className="shrink-0" aria-hidden="true" /></Button>
            <p className="mt-3 text-xs leading-6 text-muted">{i18n._(alerts ? copy.alerts_free : copy.common_free)}</p>
            {alerts && <Link href={`/${locale}/explore`} prefetch={false} className="mt-3 inline-block text-xs leading-6 text-muted underline underline-offset-4 hover:text-foreground">{i18n._(copy.alerts_noShortlist)}</Link>}
          </div>
          <div className="mx-auto h-[280px] w-full max-w-[420px] sm:h-[360px] lg:ml-auto lg:mr-0">
            <PublicDomainArt
              asset={publicDomainAssets[alerts ? "the_ploughman" : "the_emperor"]}
              loading="eager"
              fetchPriority="high"
              themeRendering="css-invert"
              sizes="(min-width: 768px) 420px, 100vw"
              className="h-full w-full"
            />
          </div>
        </section>
        <section className="mt-16 grid items-center gap-8 md:mt-24 lg:grid-cols-[0.8fr_1.2fr]">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">{i18n._(copy[`${kind}_proofTitle`])}</h2>
            <p className="mt-4 text-sm leading-7 text-muted">{i18n._(copy[`${kind}_proofBody`])}</p>
          </div>
          <figure>
          <a href={screenshot.light.replace("{lang}", locale)} aria-label={i18n._(copy.common_enlarge)} className="block overflow-hidden rounded-xl border border-border-soft">
            <ThemedImage lightSrc={screenshot.light.replace("{lang}", locale)} darkSrc={screenshot.dark.replace("{lang}", locale)} width={screenshot.width} height={screenshot.height} alt={i18n._(copy[`${kind}_imageAlt`])} sizes="(min-width: 1024px) 650px, 100vw" />
          </a>
          <figcaption className="mt-3 text-xs text-muted"><a href={screenshot.light.replace("{lang}", locale)} className="underline underline-offset-4">{i18n._(copy.common_enlarge)}</a></figcaption>
          </figure>
        </section>
        <section className="mt-16 border-t border-divider pt-10 md:mt-24">
          <h2 className="text-2xl font-semibold tracking-tight">{i18n._(copy[`${kind}_stepsTitle`])}</h2>
          <ol className="mt-8 grid gap-8 md:grid-cols-3">
            {steps.map(([title, body], index) => <li key={title.id}>
              <span className="text-xs text-muted" aria-hidden="true">0{index + 1}</span>
              <h3 className="mt-3 text-lg font-semibold">{i18n._(title)}</h3>
              <p className="mt-3 text-sm leading-7 text-muted">{i18n._(body)}</p>
            </li>)}
          </ol>
        </section>

        <section className="mt-12 grid items-center gap-8 md:mt-16 lg:grid-cols-[1.2fr_0.8fr] lg:gap-16">
          <div className="rounded-2xl border border-border-soft bg-surface p-5 sm:p-8">
            <p className="mb-6 text-[10px] font-semibold uppercase tracking-wider text-muted">{i18n._(copy.common_example)}</p>
            {alerts ? <>
              <Mail size={24} aria-hidden="true" />
              <p className="mt-4 text-xl font-semibold">{i18n._(copy.common_digestTitle)}</p>
              <p className="mt-2 text-sm leading-6 text-muted">{i18n._(copy.common_digestBody)}</p>
            </> : <ol className="flex flex-wrap gap-x-4 gap-y-2 border-b border-divider pb-5 text-xs">
              {[copy.common_saved, copy.common_applied, copy.common_interview].map((label, index) => <li key={label.id} className="flex items-center gap-2">{index < 2 ? <Check size={14} aria-hidden="true" /> : <Bookmark size={14} aria-hidden="true" />}{i18n._(label)}</li>)}
            </ol>}
            <div className="my-6 rounded-xl border border-border-soft bg-background p-5">
              <p className="text-xs text-muted">{i18n._(copy.common_exampleCompany)}</p>
              <p className="mt-2 font-semibold">{i18n._(copy.common_exampleRole)}</p>
              {!alerts && <p className="mt-4 border-t border-divider pt-4 text-sm leading-6 text-muted">{i18n._(copy.common_exampleNote)}</p>}
            </div>
            {alerts && <p className="flex items-center gap-2 text-xs leading-6 text-muted"><Bell size={14} className="shrink-0" aria-hidden="true" />{i18n._(copy.common_digestFooter)}</p>}
          </div>
          <div>
          <h2 className="text-lg font-semibold">{i18n._(copy[`${kind}_scopeTitle`])}</h2>
          <p className="mt-3 max-w-4xl text-sm leading-7 text-muted">{i18n._(copy[`${kind}_scopeBody`])}</p>
          </div>
        </section>
        <section className="mt-16 border-t border-divider pt-10">
          <h2 className="text-2xl font-semibold">{i18n._(copy.common_continue)}</h2>
          <Button href={`/${locale}/explore`} className="mt-6 max-w-full whitespace-normal! text-center">{i18n._(copy[`${kind}_cta`])}</Button>
          <div className="mt-8"><MarketingLinks locale={locale} /></div>
        </section>
      </main>
    </>
  );
}
