"use client";

import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { siteConfig } from "@/content/config";
import { useLocalePath } from "@/lib/useLocalePath";
import { eyebrowClass, sectionHeadingClass } from "@/lib/styles";
import { Button } from "@/components/ui/Button";
import { CircleCheck } from "lucide-react";

function FeatureItem({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2">
      <CircleCheck size={18} className="shrink-0 text-primary" />
      <span>{children}</span>
    </li>
  );
}

function FreeTier() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const cfg = siteConfig.pricing.free;
  const ctaHref = lp(cfg.href);
  const ctaLabel = t({ id: "home.pricing.free.cta", comment: "Free tier CTA", message: "Start for free" });

  return (
    <div className="mx-auto flex w-full max-w-[500px] md:mx-0 md:max-w-[360px] md:flex-[1_1_320px]">
      <div className="flex w-full flex-col rounded-lg border border-border-soft bg-surface">
        <div className="flex flex-1 flex-col p-6">
          <p className="text-sm font-medium text-muted">
            <Trans id="home.pricing.free.name" comment="Free tier name">Free</Trans>
          </p>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-3xl font-bold">$0</span>
            <span className="text-muted">
              <Trans id="home.pricing.free.period" comment="Free tier period">Forever</Trans>
            </span>
          </div>
          <p className="mt-2 text-muted">
            <Trans id="home.pricing.free.description" comment="Free tier description">Test Job Seek with enough headroom to be up to date with your dream companies.</Trans>
          </p>
          <ul className="mt-4 flex-1 space-y-2">
            <FeatureItem><Trans id="home.pricing.free.f1" comment="Free feature: subscribe to companies">Subscribe to up to 5 companies</Trans></FeatureItem>
            <FeatureItem><Trans id="home.pricing.free.f2" comment="Free feature: application tracker">Application tracker</Trans></FeatureItem>
            <FeatureItem><Trans id="home.pricing.free.f3" comment="Free feature: saved searches">Saved searches</Trans></FeatureItem>
          </ul>
        </div>
        <div className="px-6 pb-6">
          <Button href={ctaHref} variant="outline" className="w-full text-center">
            {ctaLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ProTier() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const cfg = siteConfig.pricing.pro;
  const ctaHref = lp(cfg.href);
  const ctaLabel = t({ id: "home.pricing.pro.cta", comment: "Pro tier CTA", message: "Upgrade to Pro" });

  return (
    <div className="mx-auto flex w-full max-w-[500px] md:mx-0 md:max-w-[360px] md:flex-[1_1_320px]">
      <div className="flex w-full flex-col rounded-lg border-2 border-primary bg-surface shadow-md">
        <div className="flex flex-1 flex-col p-6">
          <p className="text-sm font-medium text-muted">
            <Trans id="home.pricing.pro.name" comment="Pro tier name">Pro</Trans>
          </p>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-3xl font-bold">$10</span>
            <span className="text-muted">
              <Trans id="home.pricing.pro.period" comment="Pro tier period">per month</Trans>
            </span>
          </div>
          <p className="mt-2 text-muted">
            <Trans id="home.pricing.pro.description" comment="Pro tier description">For active job seekers who need unlimited reach and faster insight.</Trans>
          </p>
          <ul className="mt-4 flex-1 space-y-2">
            <FeatureItem><Trans id="home.pricing.pro.f1" comment="Pro feature: unlimited subscriptions">Unlimited company subscriptions</Trans></FeatureItem>
            <FeatureItem><Trans id="home.pricing.pro.f2" comment="Pro feature: application tracker">Application tracker</Trans></FeatureItem>
            <FeatureItem><Trans id="home.pricing.pro.f3" comment="Pro feature: saved searches">Saved searches</Trans></FeatureItem>
            <FeatureItem><Trans id="home.pricing.pro.f4" comment="Pro feature: email alerts">Email alerts & updates</Trans></FeatureItem>
          </ul>
        </div>
        <div className="px-6 pb-6">
          <Button href={ctaHref} className="w-full text-center">
            {ctaLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function Pricing() {
  return (
    <section id={siteConfig.pricing.anchorId} className="mx-auto max-w-[1200px] px-4 py-12 md:py-20">
      <div className="mx-auto flex max-w-[640px] flex-col gap-4 text-center">
        <span className={eyebrowClass}>
          <Trans id="home.pricing.eyebrow" comment="Pricing section eyebrow">Pricing</Trans>
        </span>
        <h2 className={sectionHeadingClass}>
          <Trans id="home.pricing.title" comment="Pricing section heading">Choose the right plan for you</Trans>
        </h2>
        <p className="text-muted">
          <Trans id="home.pricing.description" comment="Pricing section description">Simple, transparent pricing. Start for free and upgrade when you get serious about your job search.</Trans>
        </p>
      </div>

      <div className="mt-8 flex flex-col flex-wrap items-center justify-center gap-6 md:mt-12 md:flex-row md:items-stretch">
        <FreeTier />
        <ProTier />
      </div>
    </section>
  );
}
