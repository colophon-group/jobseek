import Link from "next/link";
import { Bookmark, ChevronDown, SlidersHorizontal, Star } from "lucide-react";
import { CompanyIcon } from "@/components/CompanyIcon";
import { ExploreRepositoryFallback } from "@/components/search/explore-repository-fallback";
import { SearchUnavailable } from "@/components/search/search-unavailable";
import { timeAgoShort } from "@/lib/time";
import type { ExploreData } from "@/lib/actions/explore-page-data";

type ExploreStaticResultsProps = {
  locale: string;
  heading: string;
  data: ExploreData;
  labels: {
    filters: string;
    allLanguages: string;
    change: string;
    companyStats: Record<string, { active: string; year: string }>;
  };
};

/**
 * Query-agnostic, non-interactive snapshot embedded in cached Explore HTML.
 *
 * The full SearchPage deliberately owns browser URL state, dialogs, infinite
 * scroll, and authenticated controls. Any one of those client hooks can make
 * Next postpone that island during prerender. This server-only representation
 * keeps company/posting content useful to crawlers and no-JS visitors without
 * duplicating the interactive state machine. ExploreContent hides it only
 * after its hydrated tree is ready.
 */
export function ExploreStaticResults({
  locale,
  heading,
  data,
  labels,
}: ExploreStaticResultsProps) {
  return (
    <section data-explore-static-results className="space-y-6">
      <h1 className="sr-only">{heading}</h1>

      <div className="space-y-3">
        <div className="flex h-8 items-center">
          <span className="inline-flex items-center gap-2 text-sm text-muted">
            <SlidersHorizontal size={16} aria-hidden="true" />
            {labels.filters}
            <ChevronDown size={14} aria-hidden="true" />
          </span>
        </div>
        <p className="text-xs text-muted">
          {labels.allLanguages}
          {" · "}
          <Link href={`/${locale}/settings`} className="text-foreground hover:underline">
            {labels.change}
          </Link>
        </p>
      </div>

      {data.repositoryFallbackCompanies?.length ? (
        <ExploreRepositoryFallback
          locale={locale}
          companies={data.repositoryFallbackCompanies}
        />
      ) : data.result.companies.length === 0 && data.result.degraded ? (
        <SearchUnavailable />
      ) : (
        <div className="space-y-3" data-explore-static-company-list>
          {data.result.companies.map(({ company, postings }) => (
            <article
              key={company.id}
              className="rounded-md border border-divider bg-surface p-4"
              data-search-result-company={company.slug}
            >
              <div className="flex items-center gap-3">
                <Link
                  href={`/${locale}/company/${company.slug}`}
                  prefetch={false}
                  className="inline-flex items-center gap-3 transition-opacity hover:opacity-80"
                >
                  <CompanyIcon icon={company.icon} alt={company.name} size={32} />
                  <span className="text-sm font-semibold">{company.name}</span>
                </Link>
                <span aria-hidden="true" className="ml-auto inline-flex size-6 items-center justify-center text-muted">
                  <Star size={18} />
                </span>
              </div>

              <p className="mt-2 text-xs text-muted">
                {labels.companyStats[company.id]?.active}
                {" · "}
                {labels.companyStats[company.id]?.year}
              </p>
              <hr className="my-3 border-divider" />
              <ul className="max-h-[184px] overflow-hidden">
                {postings.map((posting) => (
                  <li key={posting.id} className="flex min-h-7 items-center gap-2 rounded px-1 py-1.5 text-sm [contain:layout]">
                    <span aria-hidden="true" className="size-2 shrink-0 rounded-full border border-muted" />
                    <span className="min-w-0 flex-1 truncate">{posting.title ?? "—"}</span>
                    {posting.locations[0] && (
                      <span className={`shrink-0 text-xs text-muted ${posting.locations[0].geoType && posting.locations[0].geoType !== "city" ? "italic" : ""}`}>
                        {posting.locations[0].name}
                        {posting.locations.length > 1 && ` +${posting.locations.length - 1}`}
                      </span>
                    )}
                    <span aria-hidden="true" className="inline-flex size-4 shrink-0 items-center justify-center text-muted">
                      <Bookmark size={14} />
                    </span>
                    <span className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted">
                      {timeAgoShort(posting.firstSeenAt, locale)}
                    </span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
