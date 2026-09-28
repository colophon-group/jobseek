import type { Metadata } from "next";
import Link from "next/link";
import { getI18n } from "@lingui/react/server";
import { ArrowRight } from "lucide-react";
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
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-[1200px] scroll-mt-12 px-4 py-9 md:py-12">
        <section className="grid items-center gap-7 md:grid-cols-[1.15fr_1fr] lg:gap-12">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">{i18n._(copy[`${kind}_eyebrow`])}</p>
            <h1 className="mt-4 max-w-2xl text-balance break-words text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{i18n._(copy[`${kind}_title`])}</h1>
            <p className="mt-4 max-w-xl leading-7 text-muted">{i18n._(copy[`${kind}_body`])}</p>
            <Button href={`/${locale}/explore`} className="mt-5 max-w-full gap-2 whitespace-normal! text-center">{i18n._(copy[`${kind}_cta`])}<ArrowRight size={16} className="shrink-0" aria-hidden="true" /></Button>
            <p className="mt-3 text-xs leading-6 text-muted">{i18n._(alerts ? copy.alerts_free : copy.common_free)}</p>
            {alerts && <Link href={`/${locale}/explore`} prefetch={false} className="mt-3 inline-block text-xs leading-6 text-muted underline underline-offset-4 hover:text-foreground">{i18n._(copy.alerts_noShortlist)}</Link>}
          </div>
          <div className="mx-auto h-[180px] w-full max-w-[420px] sm:h-[260px] md:h-[300px] lg:ml-auto lg:mr-0">
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
        <section className="mt-10 grid items-center gap-5 md:mt-12 lg:grid-cols-[0.8fr_1.2fr]">
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
        <section className="mt-10 border-t border-divider pt-6 md:mt-12">
          <h2 className="text-2xl font-semibold tracking-tight">{i18n._(copy[`${kind}_stepsTitle`])}</h2>
          <ol className="mt-5 grid gap-5 md:grid-cols-3">
            {steps.map(([title, body], index) => <li key={title.id}>
              <span className="text-xs text-muted" aria-hidden="true">0{index + 1}</span>
              <h3 className="mt-3 text-lg font-semibold">{i18n._(title)}</h3>
              <p className="mt-3 text-sm leading-7 text-muted">{i18n._(body)}</p>
            </li>)}
          </ol>
        </section>

        <aside className="mt-8 border-l-2 border-border-soft pl-4">
          <h2 className="text-sm font-semibold">{i18n._(copy[`${kind}_scopeTitle`])}</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{i18n._(copy[`${kind}_scopeBody`])}</p>
        </aside>
        <section className="mt-10 border-t border-divider pt-6">
          <h2 className="text-2xl font-semibold">{i18n._(copy.common_continue)}</h2>
          <Button href={`/${locale}/explore`} className="mt-6 max-w-full whitespace-normal! text-center">{i18n._(copy[`${kind}_cta`])}</Button>
          <div className="mt-8"><MarketingLinks locale={locale} /></div>
        </section>
      </main>
    </>
  );
}
