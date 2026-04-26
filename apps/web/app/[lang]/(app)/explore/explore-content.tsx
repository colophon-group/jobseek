"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { fetchExploreData, type ExploreData } from "@/lib/actions/explore-data";
import { hasLoggedInHint } from "@/lib/client-cookies";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { SearchPage } from "./search-page";

type ExploreContentProps = {
  locale: string;
  /**
   * Server-prerendered ``ExploreData`` for the unauthenticated, no-filter
   * homepage case (#2640). Anonymous visitors with no filter searchParams
   * use this directly — no Vercel function invocation. When ``initialData``
   * is omitted (legacy call sites), the component falls back to the
   * client-mount fetch behaviour from before this PR.
   */
  initialData?: ExploreData;
};

/**
 * URL searchParams that ``fetchExploreData`` consumes. If any of these
 * are present, the prerendered ``initialData`` doesn't reflect the
 * filters and we must re-fetch the personalized variant.
 */
const FILTER_PARAMS = ["q", "loc", "occ", "sen", "tech", "sal", "salcur", "exp"];

function hasAnyFilterParam(searchParams: URLSearchParams): boolean {
  for (const key of FILTER_PARAMS) {
    if (searchParams.has(key)) return true;
  }
  return false;
}

export function ExploreContent({ locale, initialData }: ExploreContentProps) {
  const searchParams = useSearchParams();
  const [data, setData] = useState<ExploreData | null>(initialData ?? null);

  // Re-fetch on mount only when the prerendered ``initialData`` doesn't
  // reflect the user's actual view — i.e. they have filter searchParams
  // OR the ``logged_in`` hint cookie is present (their preferences /
  // job-language filter / display currency would change the result set).
  // Anonymous, no-filter visitors get the prerendered data with zero
  // function invocations — the bulk of organic traffic per #2640.
  useEffect(() => {
    window.scrollTo(0, 0);
    const needsPersonalizedFetch =
      hasLoggedInHint() || hasAnyFilterParam(searchParams) || initialData === undefined;
    if (!needsPersonalizedFetch) return;

    const sp: Record<string, string | undefined> = {};
    searchParams.forEach((value, key) => {
      sp[key] = value;
    });
    fetchExploreData({ searchParams: sp, locale }).then(setData);
    // Empty deps: the conditional-fetch decision is made once on
    // mount. ``initialData`` is stable across re-renders (page
    // identity), and ``SearchPage`` owns subsequent filter changes
    // via its own state — re-running this effect on ``searchParams``
    // change would clobber the user's interactive filter selection.
  }, []);

  if (!data) return <ExploreSkeleton />;

  const { result, parsed, displayCurrency, jobLanguages, languages, userLat, userLng, salaryCurrencyParam, salaryMinDisplay, salaryMaxDisplay, experienceMin, experienceMax } = data;

  return (
    <SearchPage
      initialCompanies={result.companies}
      initialTotalCompanies={result.totalCompanies}
      initialTruncated={result.truncated}
      initialKeywords={parsed.keywords}
      initialLocations={parsed.locations}
      initialOccupations={parsed.occupations}
      initialSeniorities={parsed.seniorities}
      initialTechnologies={parsed.technologies}
      initialSalaryCurrency={salaryCurrencyParam !== displayCurrency ? salaryCurrencyParam : undefined}
      initialSalaryMin={salaryMinDisplay}
      initialSalaryMax={salaryMaxDisplay}
      initialExperienceMin={experienceMin}
      initialExperienceMax={experienceMax}
      locale={locale}
      displayCurrency={displayCurrency}
      jobLanguages={jobLanguages}
      languages={languages}
      userLat={userLat}
      userLng={userLng}
    />
  );
}
