"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { fetchExploreData, type ExploreData } from "@/lib/actions/explore-data";
import { hasLoggedInHint, hasAnonJobLanguagesHint } from "@/lib/client-cookies";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { SearchPage } from "./search-page";

/**
 * Retry policy for the personalized `fetchExploreData` server action
 * on cold-start. When the Vercel function instance is cold and
 * Typesense's first-request TLS handshake takes a few hundred ms, the
 * client-side fetch can be aborted (`net::ERR_ABORTED`) by the browser
 * before the server response arrives — DevTools surfaces this as
 * "Fetch failed loading: POST". Without a client retry, the rejected
 * promise leaks out and `setData` is never called, leaving the user on
 * whatever the prerendered static cache had (potentially the empty/
 * degraded variant from issue #3008).
 *
 * Retry once with a short delay (the second call usually hits a warm
 * function instance + warm Typesense connection). If both fail,
 * surface the error so the existing initialData stays.
 */
async function fetchExploreDataWithRetry(
  args: Parameters<typeof fetchExploreData>[0],
): Promise<ExploreData> {
  try {
    return await fetchExploreData(args);
  } catch (err) {
    console.warn("[explore] fetchExploreData failed, retrying once", err);
    await new Promise((resolve) => setTimeout(resolve, 250));
    return fetchExploreData(args);
  }
}

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
const FILTER_PARAMS = ["q", "loc", "occ", "sen", "tech", "wm", "sal", "salcur", "exp"];

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
  // reflect the user's actual view — i.e. they have filter searchParams,
  // the ``logged_in`` hint cookie is present (their DB-backed
  // preferences / job-language filter / display currency would change
  // the result set), OR they have an anonymous job-language cookie
  // set (#2850 — anon viewers persist `jobLanguages` via a cookie
  // that the server side reads in `fetchExploreData`). Anonymous, no-
  // filter, no-job-lang-cookie visitors still get the prerendered data
  // with zero function invocations — the bulk of organic traffic per
  // #2640.
  useEffect(() => {
    window.scrollTo(0, 0);
    const needsPersonalizedFetch =
      hasLoggedInHint() ||
      hasAnonJobLanguagesHint() ||
      hasAnyFilterParam(searchParams) ||
      initialData === undefined;
    if (!needsPersonalizedFetch) return;

    const sp: Record<string, string | undefined> = {};
    searchParams.forEach((value, key) => {
      sp[key] = value;
    });
    fetchExploreDataWithRetry({ searchParams: sp, locale })
      .then(setData)
      .catch((err) => {
        // Both attempts failed; keep the prerendered ``initialData``
        // so the page doesn't go empty mid-render. Log so production
        // observability picks up the cold-start spike (#3008).
        console.error("[explore] fetchExploreData failed twice", err);
      });
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
      initialWorkMode={parsed.workMode}
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
