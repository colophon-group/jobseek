"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import type { CompanyPageData } from "@/lib/actions/company-page-data";
import {
  hasLoggedInHint,
  readAnonJobLanguagesPreference,
} from "@/lib/client-cookies";
import { logExternalError } from "@/lib/safe-external-error";
import { CompanySkeleton } from "@/components/search/company-skeleton";
import { useSession } from "@/components/providers/SessionProvider";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { loadCompanyBrowserData } from "@/lib/search/company-browser-data";
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
   * no second server-action round-trip on mount. The server route resolves
   * unknown slugs before this client boundary is rendered.
   */
  initialData: CompanyPageData;
};

/**
 * Result-bearing URL searchParams. If any are present, the prerendered
 * ``initialData`` does not reflect the browser state and we resolve the
 * bounded filter vocabulary plus posting results directly through Typesense.
 *
 * The shared result-bearing parameter list deliberately excludes the
 * ``show`` deep-link param: it only selects a posting in ``CompanyPage``
 * and ``JobDetailPanel`` fetches that posting independently. Treating it
 * as a data input would unmount and refetch the entire company results
 * view on every posting click (#5766).
 */
export function CompanyContent({ locale, slug, initialData }: CompanyContentProps) {
  const searchParams = useSearchParams();
  const dataParamsKey = serializeSearchFilterParams(searchParams);
  const { isLoggedIn, isPending, preferences } = useSession();
  const rates = useSalaryRates();
  const preferenceLanguagesKey = preferences?.jobLanguages?.join(",") ?? "";
  const preferenceCurrency = preferences?.displayCurrency ?? null;
  const fetchIdRef = useRef(0);
  const [view, setView] = useState<{
    data: CompanyPageData;
    unavailable: boolean;
    directAttempted: boolean;
  } | null>({ data: initialData, unavailable: false, directAttempted: false });

  // Re-initialize only when the prerendered anonymous snapshot does not
  // reflect the browser URL or viewer preferences. Authenticated preferences
  // come from the app bootstrap action the layout already paid for; anonymous
  // job languages come from their client-readable, bounded cookie. Filter
  // resolution and posting results go browser-direct to Typesense, so this
  // boundary no longer emits a company-page Server Action on mount.
  //
  // After this effect, CompanyPage owns interactive filter changes and
  // searches — URL sync via replaceState is for bookmarkability only.
  // The deps below are the page identity/snapshot inputs that can
  // change when App Router reuses this client boundary across company
  // slug or locale navigation.
  useEffect(() => {
    const fetchId = ++fetchIdRef.current;
    const params = new URLSearchParams(dataParamsKey);
    if (hasLoggedInHint() && isPending) return;

    const anonymousJobLanguages = isLoggedIn
      ? null
      : readAnonJobLanguagesPreference();
    const needsBrowserLoad =
      isLoggedIn ||
      anonymousJobLanguages !== null ||
      hasSearchFilterParams(params);
    if (!needsBrowserLoad) {
      setView({
        data: initialData,
        unavailable: false,
        directAttempted: false,
      });
      return;
    }

    // Clear stale prerendered data before the browser-direct load so
    // CompanyPage unmounts. Its filters/postings are useState-initialised
    // from props, so keeping the unfiltered ISR instance mounted would
    // ignore resolved filtered props. This also prevents a filtered URL from
    // flashing the broader unfiltered list while its scoped read is pending.
    setView(null);

    void loadCompanyBrowserData({
      initialData,
      searchParams: params,
      locale,
      displayCurrency: preferenceCurrency,
      jobLanguages:
        preferences?.jobLanguages ?? anonymousJobLanguages ?? [],
      rates,
      isLoggedIn,
    })
      .then((result) => {
        if (fetchIdRef.current !== fetchId) return;
        setView(result);
      })
      .catch((error) => {
        if (fetchIdRef.current !== fetchId) return;
        logExternalError(
          "error",
          { service: "typesense", operation: "load_company_browser_data" },
          error,
        );
        // Stay on the skeleton rather than restoring unfiltered results under
        // a filtered/personalized URL. Expected transport failures are already
        // converted to an explicit unavailable result by the loader.
      });
  }, [
    dataParamsKey,
    initialData,
    isLoggedIn,
    isPending,
    locale,
    preferenceCurrency,
    preferenceLanguagesKey,
    rates,
    slug,
  ]);

  if (view === null) return <CompanySkeleton />;
  const { data } = view;

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
      initialSearchUnavailable={view.unavailable}
      initialDirectRefreshAttempted={view.directAttempted}
    />
  );
}
