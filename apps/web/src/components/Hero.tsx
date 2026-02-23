"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useAuth } from "@/lib/useAuth";
import { siteConfig, publicDomainAssets } from "@/content/config";
import { PublicDomainArt } from "@/components/PublicDomainArt";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";

export function Hero() {
  const { isLoggedIn } = useAuth();
  const { t } = useLingui();
  const lp = useLocalePath();

  const primaryHref = isLoggedIn ? lp(siteConfig.nav.dashboard.href) : lp(siteConfig.nav.login.href);
  const primaryLabel = isLoggedIn
    ? t({ id: "common.dashboard.goTo", comment: "CTA when logged in: go to dashboard", message: "Go to dashboard" })
    : t({ id: "home.hero.primaryCta", comment: "Hero primary call-to-action", message: "Get started" });

  const heroArt = publicDomainAssets[siteConfig.hero.art.assetKey];
  const heroArtFocus = siteConfig.hero.art.focus;

  return (
    <section className="mx-auto max-w-[1200px] px-4 py-16 md:py-24">
      <div className="flex flex-col items-stretch gap-12 md:flex-row md:gap-20">
        <div className="flex flex-1 flex-col gap-6">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">
            <Trans id="home.hero.eyebrow" comment="Hero eyebrow text above the title">Keep your hand on the job market pulse.</Trans>
          </span>
          <h1 className="text-3xl font-bold md:text-4xl">
            <Trans id="home.hero.title" comment="Main heading on the landing page">Find relevant roles faster.</Trans>
          </h1>
          <p className="text-muted">
            <Trans id="home.hero.description" comment="Hero description paragraph">
              Subscribe to updates from companies, track applications, and never miss new openings. Designed to keep you in control, not hand your decisions to a bot.
            </Trans>
          </p>
          <div className="flex flex-col gap-4 pt-4 sm:flex-row">
            <Button href={primaryHref}>
              {primaryLabel}
            </Button>
            <Button href={lp(siteConfig.nav.features.href)} variant="outline">
              <Trans id="home.hero.secondaryCta" comment="Hero secondary call-to-action">Learn more</Trans>
            </Button>
          </div>
        </div>

        {heroArt && (
          <div className="h-[280px] w-full sm:h-[340px] md:h-auto md:min-w-[360px] md:max-w-[420px] md:flex-[1_1_360px]">
            <PublicDomainArt
              asset={heroArt}
              focus={heroArtFocus}
              crop={{ top: 100, bottom: 100, left: 0, right: 0 }}
              className="h-full w-full"
            />
          </div>
        )}
      </div>
    </section>
  );
}
