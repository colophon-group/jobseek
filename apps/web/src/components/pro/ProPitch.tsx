"use client";

import { Trans } from "@lingui/react/macro";
import { ArrowDown, Check, SlidersHorizontal, X } from "lucide-react";
import { useLocalePath } from "@/lib/useLocalePath";

export function BillingPolicyLinks() {
  const lp = useLocalePath();
  return <p className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted">
    <a href={lp("/terms")} className="underline underline-offset-4"><Trans id="common.footer.termsLink" comment="Footer link to terms of service page">Terms</Trans></a>
    <a href={lp("/privacy-policy")} className="underline underline-offset-4"><Trans id="common.footer.privacyLink" comment="Footer link to privacy policy page">Privacy</Trans></a>
    <a href={lp("/terms#refund-policy")} className="underline underline-offset-4"><Trans id="common.footer.refundsLink" comment="Footer link to refund policy">Refunds</Trans></a>
  </p>;
}

/** A clearly labeled example of the product, shared by discovery and billing. */
export function ProPitch() {
  return (
    <div className="grid items-center gap-8 lg:grid-cols-[1fr_1.05fr] lg:gap-12">
      <div>
        <p className="mb-5 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted">
          <SlidersHorizontal size={14} aria-hidden="true" /> Job Seek Pro <span className="text-border-soft" aria-hidden="true">/</span> <Trans id="pro.pitch.feature" comment="Name of the Pro feature; keep the product name Narrowed">Narrowed</Trans>
        </p>
        <h2 className="text-3xl font-semibold leading-[1.2] tracking-tight sm:text-4xl">
          <Trans id="pro.pitch.title" comment="Pro headline: personal criteria lead to fewer, more relevant jobs">Your criteria.<br />A shorter list.</Trans>
        </h2>
        <p className="mt-5 max-w-md text-sm leading-7 text-muted">
          <Trans id="pro.pitch.body" comment="Explains the Narrowed benefit">Describe what matters beyond job titles. Narrowed checks the postings in your watchlist against your criteria, so you can focus on the matches.</Trans>
        </p>
        <p className="mt-5 text-xs leading-6 text-muted">
          <Trans id="pro.pitch.scope" comment="Explains that Narrowed adds to standard search rather than replacing it">Checks the information in postings. Missing details do not confirm that a role meets your criteria.</Trans>
        </p>
        <a href="#pro-offer" className="mt-5 inline-flex items-center gap-2 text-sm underline underline-offset-4 lg:hidden">
          <Trans id="pro.pitch.pricingLink" comment="Mobile shortcut from product explanation to pricing">See pricing</Trans><ArrowDown size={14} aria-hidden="true" />
        </a>
      </div>
      <div className="rounded-2xl border border-border-soft bg-surface p-5 sm:p-6">
        <p className="mb-4 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
          <Trans id="pro.example.label" comment="Labels the fictional Narrowed demonstration">An example</Trans>
        </p>
        <p className="mb-2 text-xs text-muted"><Trans id="pro.example.requestLabel" comment="Label for the example natural-language filter">Your criteria</Trans></p>
        <p className="border-l-2 border-foreground pl-3 text-sm leading-6">
          <Trans id="pro.example.request" comment="Fictional semantic criteria for Narrowed, beyond titles and keywords">Hands-on ownership from design to production, without people management—even if the title says senior or staff.</Trans>
        </p>
        <div className="my-4 flex items-center gap-2 text-xs text-muted">
          <ArrowDown size={14} aria-hidden="true" />
          <Trans id="pro.example.check" comment="Describes the example matching step">Checked against the job description</Trans>
        </div>
        <div className="space-y-2">
          <div className="rounded-lg border border-success-border bg-success-bg p-3">
            <div className="mb-2 flex items-center justify-between gap-2 text-xs">
              <span className="font-medium"><Trans id="pro.example.role" comment="Fictional role in the Pro illustration">Senior software engineer</Trans></span>
              <span className="inline-flex items-center gap-1 text-success"><Check size={13} aria-hidden="true" /><Trans id="pro.example.match" comment="Example matching result">Match</Trans></span>
            </div>
            <p className="text-xs leading-5 text-muted-strong"><Trans id="pro.example.matchText" comment="Fictional excerpt illustrating a match">“Own features from discovery to launch. Senior individual contributor.”</Trans></p>
          </div>
          <div className="rounded-lg border border-border-soft p-3">
            <div className="mb-2 flex items-center justify-between gap-2 text-xs">
              <span className="font-medium"><Trans id="pro.example.role" comment="Fictional role in the Pro illustration">Senior software engineer</Trans></span>
              <span className="inline-flex items-center gap-1 text-muted"><X size={13} aria-hidden="true" /><Trans id="pro.example.excluded" comment="Example excluded result">Filtered out</Trans></span>
            </div>
            <p className="text-xs leading-5 text-muted"><Trans id="pro.example.excludedText" comment="Fictional excerpt illustrating an exclusion">“Lead eight engineers. Own hiring and performance reviews.”</Trans></p>
          </div>
        </div>
      </div>
    </div>
  );
}

export function FreeAccessNote() {
  return (
    <div className="flex items-start gap-3 border-t border-divider pt-5 text-xs leading-6 text-muted">
      <Check size={16} className="mt-1 shrink-0 text-success" aria-hidden="true" />
      <p><span className="font-semibold text-foreground"><Trans id="pro.free.title" comment="Reassures that core job search features stay free">Your job search stays free.</Trans></span>{" "}<Trans id="pro.free.body" comment="Features everyone has, independent of Pro">Full search, up to 10 watchlists, weekly email digests, and tracking for jobs saved on Job Seek. Pro adds Narrowed results.</Trans></p>
    </div>
  );
}
