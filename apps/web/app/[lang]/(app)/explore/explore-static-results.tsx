import Link from "next/link";
import { CompanyIcon } from "@/components/CompanyIcon";
import type { ExploreData } from "@/lib/actions/explore-page-data";

type ExploreStaticResultsProps = {
  locale: string;
  heading: string;
  data: ExploreData;
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
export function ExploreStaticResults({ locale, heading, data }: ExploreStaticResultsProps) {
  return (
    <section data-explore-static-results className="space-y-6">
      <h1 className="sr-only">{heading}</h1>

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
            </div>

            <hr className="my-3 border-divider" />
            <ul className="space-y-1.5">
              {postings.map((posting) => (
                <li key={posting.id} className="flex min-h-7 items-center gap-2 px-1 py-1.5 text-sm">
                  <span className="min-w-0 flex-1 truncate">{posting.title ?? "—"}</span>
                  {posting.locations[0] && (
                    <span className="shrink-0 text-xs text-muted">
                      {posting.locations[0].name}
                      {posting.locations.length > 1 && ` +${posting.locations.length - 1}`}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </section>
  );
}
