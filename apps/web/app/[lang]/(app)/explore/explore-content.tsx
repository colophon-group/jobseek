"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ExploreData } from "@/lib/actions/explore-page-data";
import {
  hasLoggedInHint,
  hasCookieNamed,
  JOB_LANGUAGES_COOKIE,
  readAnonJobLanguagesPreference,
} from "@/lib/client-cookies";
import { logExternalError } from "@/lib/safe-external-error";
import { hasSearchFilterParams } from "@/lib/search/query-params";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { useSession } from "@/components/providers/SessionProvider";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { loadExploreBrowserData } from "@/lib/search/explore-browser-data";
import { useBrowserSearchParams } from "@/lib/use-browser-search-params";
import { SearchPage } from "./search-page";

type ExploreContentProps = {
  locale: string;
  /**
   * Server-prerendered ``ExploreData`` for the anonymous, unfiltered page
   * (#2640). Those visitors use it without a Vercel function invocation.
   * Preference- and filter-bearing views replace this query-agnostic shell
   * from browser-direct Typesense reads.
   */
  initialData: ExploreData;
};

export function ExploreContent({ locale, initialData }: ExploreContentProps) {
  const fetchIdRef = useRef(0);
  const loadKeyRef = useRef<string | null>(null);
  const viewerKeyRef = useRef<string | null>(null);
  const initializationCompleteRef = useRef(false);
  const { isLoggedIn, isPending, preferences } = useSession();
  const rates = useSalaryRates();
  const browserSearchParams = useBrowserSearchParams();
  const browserSearchKey = browserSearchParams.toString();
  const preferenceLanguagesKey = preferences?.jobLanguages?.join(",") ?? "";
  const preferenceCurrency = preferences?.displayCurrency ?? null;
  const [view, setView] = useState<{
    data: ExploreData;
    unavailable: boolean;
    directAttempted: boolean;
  } | null>({ data: initialData, unavailable: false, directAttempted: false });

  // The parser-time guard covers hard reloads. This layout effect covers SPA
  // navigation into Explore before React can paint the cached default feed.
  useLayoutEffect(() => {
    if (initializationCompleteRef.current) return;
    const searchParams = new URLSearchParams(window.location.search);
    if (
      hasLoggedInHint() ||
      hasCookieNamed(document.cookie, JOB_LANGUAGES_COOKIE) ||
      hasSearchFilterParams(searchParams) ||
      searchParams.has("lang")
    ) {
      document.documentElement.setAttribute("data-explore-pending", "");
    }
  }, [browserSearchKey]);

  // Re-initialize only when the query-agnostic shell does not reflect the
  // browser URL or viewer preferences. Authenticated preferences come from
  // the shared app bootstrap action; anonymous language state is bounded and
  // parsed from its client-readable cookie. Result data and explicit taxonomy
  // resolution stay browser-direct, with only semantic free text retaining a
  // narrow geo-aware parser action.
  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    if (hasLoggedInHint() && isPending) {
      // Invalidate any prior viewer-state request while the hinted session is
      // unresolved. The eventual authenticated/anonymous key will start a new
      // request once bootstrap settles.
      fetchIdRef.current += 1;
      initializationCompleteRef.current = false;
      setView(null);
      return;
    }

    const anonymousJobLanguages = isLoggedIn
      ? null
      : readAnonJobLanguagesPreference();
    const viewerKey = [
      locale,
      isLoggedIn ? "authenticated" : "anonymous",
      preferenceCurrency ?? "",
      preferences?.jobLanguages?.join(",") ??
        anonymousJobLanguages?.join(",") ??
        "",
    ].join("|");
    const loadKey = `${viewerKey}|${searchParams.toString()}`;
    if (loadKeyRef.current === loadKey) return;
    const viewerChanged =
      viewerKeyRef.current !== null && viewerKeyRef.current !== viewerKey;

    // Once SearchPage is mounted it exclusively owns URL changes and their
    // result reads. The outer subscription exists only so navigation can
    // replace an in-flight initialization safely; re-running it afterward
    // would duplicate SearchPage's request and discard its local state.
    if (initializationCompleteRef.current && !viewerChanged) {
      loadKeyRef.current = loadKey;
      return;
    }
    viewerKeyRef.current = viewerKey;
    loadKeyRef.current = loadKey;
    // Allocate the stale-result guard only after same-key dedupe. Anonymous
    // bootstrap changes isPending without changing this key; incrementing
    // before the return would discard the still-valid in-flight result and
    // leave the filtered shell on its skeleton forever.
    const fetchId = ++fetchIdRef.current;
    const needsBrowserLoad =
      isLoggedIn ||
      anonymousJobLanguages !== null ||
      hasSearchFilterParams(searchParams) ||
      searchParams.has("lang");
    if (!needsBrowserLoad) {
      initializationCompleteRef.current = true;
      setView({
        data: initialData,
        unavailable: false,
        directAttempted: false,
      });
      return;
    }

    // Unmount SearchPage before the browser load: its state is initialized
    // from props, and a filtered URL must never retain the broader shell.
    initializationCompleteRef.current = false;
    document.documentElement.setAttribute("data-explore-pending", "");
    setView(null);
    void loadExploreBrowserData({
      initialData,
      searchParams,
      locale,
      displayCurrency: preferenceCurrency,
      jobLanguages:
        preferences?.jobLanguages ?? anonymousJobLanguages ?? [],
      rates,
      isLoggedIn,
    })
      .then((result) => {
        if (fetchIdRef.current !== fetchId) return;
        initializationCompleteRef.current = true;
        setView(result);
      })
      .catch((err) => {
        if (fetchIdRef.current !== fetchId) return;
        logExternalError(
          "error",
          { service: "typesense", operation: "load_explore_browser_data" },
          err,
        );
        // Expected transport/degradation paths are converted into explicit
        // unavailable data by the loader. An unexpected failure stays on the
        // skeleton rather than restoring unfiltered shell results.
      });
  }, [
    initialData,
    browserSearchKey,
    isLoggedIn,
    isPending,
    locale,
    preferenceCurrency,
    preferenceLanguagesKey,
    rates,
  ]);

  // Reveal the single result tree only after the URL/viewer-specific data has
  // committed. Keep the skeleton visible across the whole direct read.
  useLayoutEffect(() => {
    if (view && initializationCompleteRef.current) {
      document.documentElement.removeAttribute("data-explore-pending");
    }
  }, [view]);

  useEffect(() => () => {
    document.documentElement.removeAttribute("data-explore-pending");
  }, []);

  if (!view) {
    return (
      <div data-explore-content-root>
        <ExploreSkeleton />
      </div>
    );
  }
  const { data } = view;

  const {
    result,
    repositoryFallbackCompanies,
    parsed,
    displayCurrency,
    jobLanguages,
    languages,
    languageOverride,
    userLat,
    userLng,
    salaryCurrencyParam,
    salaryMinDisplay,
    salaryMaxDisplay,
    experienceMin,
    experienceMax,
  } = data;

  return (
    <div data-explore-content-root>
      <SearchPage
        initialCompanies={result.companies}
        initialTotalCompanies={result.totalCompanies}
        initialNextOffset={result.nextOffset}
        initialTotalPostings={result.totalPostings}
        initialTruncated={result.truncated}
        initialDegraded={result.degraded}
        initialRepositoryFallbackCompanies={repositoryFallbackCompanies}
        initialKeywords={parsed.keywords}
        initialLocations={parsed.locations}
        initialOccupations={parsed.occupations}
        initialSeniorities={parsed.seniorities}
        initialTechnologies={parsed.technologies}
        initialUnresolvedExplicitSlugs={parsed.unresolvedExplicitSlugs}
        initialEmploymentTypes={parsed.employmentTypes}
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
        initialLanguageOverride={languageOverride}
        userLat={userLat}
        userLng={userLng}
        initialDirectRefreshAttempted={view.directAttempted}
      />
    </div>
  );
}
