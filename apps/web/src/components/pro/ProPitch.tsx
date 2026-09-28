"use client";

import { Trans, useLingui } from "@lingui/react/macro";
import { ArrowDown, Check, SlidersHorizontal } from "lucide-react";
import { AiSearchFilter } from "@/components/search/ai-search-filter";
import { CompanyIcon } from "@/components/CompanyIcon";
import recordedResults from "@/content/narrowed-results.json";
import { useLocalePath } from "@/lib/useLocalePath";

export function BillingPolicyLinks() {
  const lp = useLocalePath();
  return <p className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted">
    <a href={lp("/terms")} className="underline underline-offset-4"><Trans id="common.footer.termsLink" comment="Footer link to terms of service page">Terms</Trans></a>
    <a href={lp("/privacy-policy")} className="underline underline-offset-4"><Trans id="common.footer.privacyLink" comment="Footer link to privacy policy page">Privacy</Trans></a>
    <a href={lp("/terms#refund-policy")} className="underline underline-offset-4"><Trans id="common.footer.refundsLink" comment="Footer link to refund policy">Refunds</Trans></a>
  </p>;
}

/** The same read-only Narrowed drawer used by shared watchlists. */
export function ProPitch({ previewOnly = false }: { previewOnly?: boolean }) {
  const { i18n, t } = useLingui();
  const locale = i18n.locale in recordedResults ? i18n.locale as keyof typeof recordedResults : "en";
  const postings = recordedResults[locale];
  const query = t({ id: "pro.example.request", comment: "Live-tested personal criteria for Narrowed", message: "I want to work directly with users and turn their problems into product improvements. No people management." });
  return (
    <div className={previewOnly ? undefined : "grid items-center gap-8 lg:grid-cols-[1fr_1.05fr] lg:gap-12"}>
      {!previewOnly && <div>
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
      </div>}
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        readOnly
        defaultOpen
        presentation="drawer"
        initialQuery={query}
        narrowedResultCount={postings.length}
        drawerContent={<ul className="divide-y divide-divider py-2">
          {postings.map(posting => <li key={posting.id}>
            <a href={posting.sourceUrl} target="_blank" rel="noopener noreferrer" className="flex min-w-0 items-center gap-3 rounded-md px-2 py-3 transition-colors hover:bg-border-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              <CompanyIcon icon={posting.company.icon} alt={posting.company.name} size={24} />
              <span className="min-w-0 flex-1">
                <span className="block text-[10px] text-muted">{posting.company.name}</span>
                <span className="block text-sm leading-5">{posting.title}</span>
                <span className="mt-0.5 block text-[10px] text-muted">{[...new Set(posting.locationNames)].join(" · ")}</span>
              </span>
            </a>
          </li>)}
        </ul>}
      />
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
