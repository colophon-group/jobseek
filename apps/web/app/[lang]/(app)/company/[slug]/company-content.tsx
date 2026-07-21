"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { fetchCompanyPageData, type CompanyPageData } from "@/lib/actions/company-page-data";
import { hasLoggedInHint, hasAnonJobLanguagesHint } from "@/lib/client-cookies";
import { CompanySkeleton } from "@/components/search/company-skeleton";
import {
  hasSearchFilterParams,
  serializeSearchFilterParams,
} from "@/lib/search/query-params";
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
 * The shared result-bearing parameter list deliberately excludes the
 * ``show`` deep-link param: it only selects a posting in ``CompanyPage``
 * and ``JobDetailPanel`` fetches that posting independently. Treating it
 * as a data input would unmount and refetch the entire company results
 * view on every posting click (#5766).
 */
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
  const dataParamsKey = serializeSearchFilterParams(searchParams);
  const fetchIdRef = useRef(0);
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
  // After this effect, CompanyPage owns interactive filter changes and
  // searches — URL sync via replaceState is for bookmarkability only.
  // The deps below are the page identity/snapshot inputs that can
  // change when App Router reuses this client boundary across company
  // slug or locale navigation.
  useEffect(() => {
    const fetchId = ++fetchIdRef.current;
    const params = new URLSearchParams(dataParamsKey);
    const needsPersonalizedFetch =
      hasLoggedInHint() ||
      hasAnonJobLanguagesHint() ||
      hasSearchFilterParams(params) ||
      initialData === undefined;
    if (!needsPersonalizedFetch) {
      setData(initialData ?? null);
      return;
    }

    // Clear stale prerendered data before the personalised fetch so
    // CompanyPage unmounts. Its filters/postings are useState-initialised
    // from props, so keeping the unfiltered ISR instance mounted would
    // ignore the filtered props when this fetch resolves.
    setData(null);

    const sp: Record<string, string | undefined> = {};
    params.forEach((value, key) => {
      sp[key] = value;
    });
    fetchCompanyPageData({ slug, searchParams: sp, locale }).then((result) => {
      if (fetchIdRef.current !== fetchId) return;
      setData(result ?? "not-found");
    }).catch((err) => {
      if (fetchIdRef.current !== fetchId) return;
      console.error("[company] fetchCompanyPageData failed", err);
      if (initialData) setData(initialData);
    });
  }, [dataParamsKey, initialData, locale, slug]);

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
