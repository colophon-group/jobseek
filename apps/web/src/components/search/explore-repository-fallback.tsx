"use client";

import Link from "next/link";
import { useId } from "react";
import { Trans } from "@lingui/react/macro";

import { CompanyIcon } from "@/components/CompanyIcon";
import type { ExploreRepositoryCompany } from "@/lib/explore-repository-fallback";

type ExploreRepositoryFallbackProps = {
  locale: string;
  companies: ExploreRepositoryCompany[];
};

/**
 * Offline-safe company discovery. This is intentionally a different visual
 * and data shape from live search results: there are no job rows, activity
 * counts, save controls, or synthetic database identifiers.
 */
export function ExploreRepositoryFallback({
  locale,
  companies,
}: ExploreRepositoryFallbackProps) {
  const headingId = useId();

  return (
    <section
      aria-labelledby={headingId}
      data-explore-repository-fallback
      className="space-y-4"
    >
      <div role="alert" className="rounded-md border border-divider bg-surface p-4">
        <h2 id={headingId} className="text-sm font-semibold">
          <Trans
            id="explore.fallback.heading"
            comment="Accessible heading shown when live Explore job results are unavailable and repository-owned company profile links are shown instead"
          >
            Live job results are temporarily unavailable.
          </Trans>
        </h2>
        <p className="mt-1 text-sm text-muted">
          <Trans
            id="explore.fallback.body"
            comment="Try-again guidance shown above company profile links when live Explore job results are unavailable"
          >
            Try refreshing the page. In the meantime, you can explore these company profiles.
          </Trans>
        </p>
      </div>

      <ul className="grid gap-3 sm:grid-cols-2" data-explore-fallback-company-list>
        {companies.map((company) => (
          <li
            key={company.slug}
            className="rounded-md border border-divider bg-surface p-4"
            data-search-result-company={company.slug}
          >
            <Link
              href={`/${locale}/company/${company.slug}`}
              prefetch={false}
              className="inline-flex items-center gap-3 transition-opacity hover:opacity-80"
            >
              <CompanyIcon icon={null} alt={company.name} size={32} />
              <span className="text-sm font-semibold">{company.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
