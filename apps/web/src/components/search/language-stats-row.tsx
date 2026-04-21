"use client";

import { Trans } from "@lingui/react/macro";
import { LanguageNote } from "@/components/search/language-note";

type Props = {
  jobLanguages: string[];
  locale: string;
  activeCount: number;
  yearCount: number;
};

/**
 * Shared "Showing jobs in {locales} · change   {N} active · {M} in the last year"
 * row, used on the company page and watchlist view page.
 *
 * Responsive:
 * - md+: language note on the left, stats on the right (single row)
 * - sm:  language note alone, stats drop to a dedicated second row with
 *        active-left / year-right (explicitly requested on mobile so
 *        the two high-signal numbers mirror the page's own left/right
 *        reading flow).
 *
 * Numbers are locale-formatted (`toLocaleString`) so the thousands
 * separator follows the page locale.
 */
export function LanguageStatsRow({ jobLanguages, locale, activeCount, yearCount }: Props) {
  const active = activeCount.toLocaleString(locale);
  const year = yearCount.toLocaleString(locale);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4">
        <LanguageNote jobLanguages={jobLanguages} locale={locale} />
        <p className="hidden whitespace-nowrap text-xs text-muted md:block">
          {active}{" "}
          <Trans id="common.stats.active" comment="Active postings count">active</Trans>
          {" · "}
          {year}{" "}
          <Trans id="common.stats.yearCount" comment="Postings seen in the last year count">
            in the last year
          </Trans>
        </p>
      </div>
      <div className="flex items-center justify-between text-xs text-muted md:hidden">
        <span>
          {active}{" "}
          <Trans id="common.stats.active" comment="Active postings count">active</Trans>
        </span>
        <span>
          {year}{" "}
          <Trans id="common.stats.yearCount" comment="Postings seen in the last year count">
            in the last year
          </Trans>
        </span>
      </div>
    </div>
  );
}
