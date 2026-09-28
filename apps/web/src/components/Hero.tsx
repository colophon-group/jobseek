"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";

export function Hero() {
  const { t } = useLingui();
  const lp = useLocalePath();

  const primaryHref = lp(siteConfig.nav.app.href);
  const primaryLabel = t({ id: "home.hero.primaryCta", comment: "Hero primary call-to-action", message: "Find companies to follow" });

  const heroArt = publicDomainAssets[siteConfig.hero.art.assetKey];
  const heroArtFocus = siteConfig.hero.art.focus;

  return (
    <section className="mx-auto max-w-[1200px] px-4 py-16 md:py-24">
      <div className="flex flex-col items-stretch gap-12 md:flex-row md:gap-20">
        <div className="flex min-w-0 flex-1 flex-col gap-6">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">
            <Trans id="home.hero.eyebrow" comment="Hero eyebrow above the title — speaks to the targeted job seeker ICP">A focused job search, built around your preferences.</Trans>
          </span>
          <h1 className="text-3xl font-bold md:text-4xl">
            <Trans id="home.hero.title" comment="Main heading on the landing page — leads with company-watchlist ICP">Track the companies you actually want to work at.</Trans>
          </h1>
          <p className="text-muted">
            <Trans id="home.hero.description" comment="Hero description paragraph — watchlist + alerts pitch with direct sourcing as supporting claim">Find jobs from company career pages, save the employers and filters that matter to you, and track the roles you save on Job Seek.</Trans>
          </p>
          <div className="flex flex-col flex-wrap gap-4 pt-4 sm:flex-row">
            <Button href={primaryHref} className="whitespace-normal! text-center">
              {primaryLabel}
            </Button>
            <Button href={lp(siteConfig.nav.features.href)} prefetch={false} variant="outline" className="whitespace-normal! text-center">
              <Trans id="home.hero.secondaryCta" comment="Hero secondary call-to-action">See how watchlists work</Trans>
            </Button>
          </div>
          <p className="text-xs leading-6 text-muted">
            <Trans id="home.hero.freeNote" comment="Free features beside the homepage primary action">Free search. Up to 10 watchlists. Application tracking.</Trans>
          </p>
        </div>

        {heroArt && (
          <div className="h-[280px] w-full sm:h-[340px] md:h-auto md:min-w-[360px] md:max-w-[420px] md:flex-[1_1_360px]">
            <PublicDomainArt
              asset={heroArt}
              focus={heroArtFocus}
              crop={{ top: 100, bottom: 100, left: 0, right: 0 }}
              loading="eager"
              fetchPriority="high"
              themeRendering="css-invert"
              className="h-full w-full"
            />
          </div>
        )}
      </div>
    </section>
  );
}
