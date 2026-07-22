"use client";

import { LanguageNote } from "@/components/search/language-note";
import { ActivePostingCount, YearPostingCount } from "@/components/search/posting-count-labels";

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
 * Counts are rendered through ICU plurals so both number formatting and
 * grammar follow the active locale.
 */
export function LanguageStatsRow({ jobLanguages, locale, activeCount, yearCount }: Props) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4">
        <LanguageNote jobLanguages={jobLanguages} locale={locale} />
        <p className="hidden whitespace-nowrap text-xs text-muted md:block">
          <ActivePostingCount count={activeCount} />
          {" · "}
          <YearPostingCount count={yearCount} />
        </p>
      </div>
      <div className="flex items-center justify-between text-xs text-muted md:hidden">
        <span>
          <ActivePostingCount count={activeCount} />
        </span>
        <span>
          <YearPostingCount count={yearCount} />
        </span>
      </div>
    </div>
  );
}
