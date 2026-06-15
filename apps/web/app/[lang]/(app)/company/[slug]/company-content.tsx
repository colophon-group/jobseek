"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { fetchCompanyPageData, type CompanyPageData } from "@/lib/actions/company-page-data";
import { hasLoggedInHint, hasAnonJobLanguagesHint } from "@/lib/client-cookies";
import { CompanySkeleton } from "@/components/search/company-skeleton";
import { CompanyPage } from "./company-page";

type CompanyContentProps = {
  locale: string;
  slug: string;
  /**
   * Server-prerendered ``CompanyPageData`` for the unauthenticated,
   * no-filter visit case (#3203, mirrors `/explore` from #2640).
   * Anonymous visitors with no filter searchParams use this directly —
   * no second server-action round-trip on mount. When ``initialData``
   * is omitted (legacy call sites or null-from-server signalling a
   * ghost slug), the component falls back to the client-mount fetch
   * behaviour from before this PR.
   */
  initialData?: CompanyPageData;
};

/**
 * URL searchParams that ``fetchCompanyPageData`` consumes. If any of
 * these are present, the prerendered ``initialData`` doesn't reflect
 * the filters and we must re-fetch the personalized variant.
 *
 * Mirrors the list in `explore-content.tsx` (`FILTER_PARAMS`). Also
 * includes ``show`` — the deep-link param that opens a posting detail
 * panel — because it changes the rendered subtree even though it
 * doesn't affect the postings list itself. Better to refetch and keep
 * the panel responsive than to render with ``initialData`` and have
 * the panel pop in late.
 */
const FILTER_PARAMS = ["q", "loc", "occ", "sen", "tech", "wm", "etype", "sal", "salcur", "exp", "show"];

function hasAnyFilterParam(searchParams: URLSearchParams): boolean {
  for (const key of FILTER_PARAMS) {
    if (searchParams.has(key)) return true;
  }
  return false;
}

function CompanyNotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">
        <Trans
          id="company.notFound.title"
          comment="Heading shown when the company URL slug doesn't resolve to a known company"
        >
          Company not found
        </Trans>
      </h1>
      <p className="mt-2 text-muted">
        <Trans
          id="company.notFound.body"
          comment="Body text for the company-not-found page; explains the company is either gone or never existed"
        >
          The company you are looking for does not exist or has been removed.
        </Trans>
      </p>
    </div>
  );
}

export function CompanyContent({ locale, slug, initialData }: CompanyContentProps) {
  const searchParams = useSearchParams();
  const [data, setData] = useState<CompanyPageData | null | "not-found">(initialData ?? null);

  // Re-fetch on mount only when the prerendered ``initialData``
  // doesn't reflect the user's actual view — i.e. they have filter
  // searchParams, the ``logged_in`` hint cookie is present (their
  // DB-backed preferences / job-language filter / display currency
  // would change the result set), OR they have an anonymous
  // job-language cookie set (#2850 — anon viewers persist
  // `jobLanguages` via a cookie that the server side reads in
  // `fetchCompanyPageData`). Anonymous, no-filter, no-job-lang-cookie
  // visitors still get the prerendered data with zero server-action
  // invocations — the bulk of organic traffic per #3203 + #2640.
  //
  // After this effect, CompanyPage owns all filter changes and
  // searches — URL sync via replaceState is for bookmarkability only
  // and does not trigger a re-fetch here.
  useEffect(() => {
    window.scrollTo(0, 0);
    const needsPersonalizedFetch =
      hasLoggedInHint() ||
      hasAnonJobLanguagesHint() ||
      hasAnyFilterParam(searchParams) ||
      initialData === undefined;
    if (!needsPersonalizedFetch) return;

    // Clear stale prerendered data before the personalised fetch so
    // CompanyPage unmounts. Its filters/postings are useState-initialised
    // from props, so keeping the unfiltered ISR instance mounted would
    // ignore the filtered props when this fetch resolves.
    setData(null);

    const sp: Record<string, string | undefined> = {};
    searchParams.forEach((value, key) => {
      sp[key] = value;
    });
    fetchCompanyPageData({ slug, searchParams: sp, locale }).then((result) => {
      setData(result ?? "not-found");
    }).catch((err) => {
      console.error("[company] fetchCompanyPageData failed", err);
      if (initialData) setData(initialData);
    });
    // Empty deps: the conditional-fetch decision is made once on
    // mount. ``initialData`` is stable across re-renders (page
    // identity), and ``CompanyPage`` owns subsequent filter changes
    // via its own state — re-running this effect on ``searchParams``
    // change would clobber the user's interactive filter selection.
  }, []);

  if (data === null) return <CompanySkeleton />;
  if (data === "not-found") return <CompanyNotFound />;

  return (
    <CompanyPage
      company={data.company}
      initialPostings={data.postings}
      initialActiveCount={data.activeCount}
      initialYearCount={data.yearCount}
      initialTruncated={data.truncated}
      initialKeywords={data.parsed.keywords}
      initialLocations={data.parsed.locations}
      initialOccupations={data.parsed.occupations}
      initialSeniorities={data.parsed.seniorities}
      initialTechnologies={data.parsed.technologies}
      initialEmploymentTypes={data.parsed.employmentTypes}
      initialWorkMode={data.parsed.workMode}
      initialSalaryCurrency={data.salaryCurrencyParam !== data.displayCurrency ? data.salaryCurrencyParam : undefined}
      initialSalaryMin={data.salaryMinDisplay}
      initialSalaryMax={data.salaryMaxDisplay}
      initialExperienceMin={data.experienceMin}
      initialExperienceMax={data.experienceMax}
      initialShowPostingId={data.showPostingId}
      displayCurrency={data.displayCurrency}
      locale={locale}
      jobLanguages={data.jobLanguages}
      languages={data.languages}
      userLat={data.userLat}
      userLng={data.userLng}
    />
  );
}
