"use client";

import { Trans, useLingui } from "@lingui/react/macro";
import { ArrowRight } from "lucide-react";
import { siteConfig } from "@/content/config";
import { useLocalePath } from "@/lib/useLocalePath";
import { sectionScrollMarginClass } from "@/lib/styles";
import { Button } from "@/components/ui/Button";
import { FreeAccessNote, ProPitch } from "@/components/pro/ProPitch";

export function Pricing() {
  const { t } = useLingui();
  const lp = useLocalePath();
  return (
    <section id={siteConfig.pricing.anchorId} className={`mx-auto max-w-[1200px] px-4 py-16 md:py-24 ${sectionScrollMarginClass}`}>
      <div className="mx-auto max-w-5xl">
        <ProPitch />
        <div id="pro-offer" className="scroll-mt-36 my-8 flex flex-col gap-5 rounded-xl border border-border-soft px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-semibold"><Trans id="pro.discovery.trial" comment="Public Pro pricing offer">7 days to try Pro.</Trans></p>
            <p className="mt-1 text-sm text-muted"><Trans id="pro.discovery.price" comment="Public monthly price after trial">Then US$10 per month. Cancel anytime.</Trans></p>
          </div>
          <Button href={lp("/settings/billing")} className="gap-2 self-start sm:self-auto">
            {t({ id: "pro.discovery.cta", comment: "Link to the Pro explanation and subscription offer", message: "Explore Pro" })}<ArrowRight size={16} aria-hidden="true" />
          </Button>
        </div>
        <FreeAccessNote />
      </div>
    </section>
  );
}
