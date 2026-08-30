"use client";

import { useEffect, useRef, useState } from "react";
import type { ExploreData } from "@/lib/actions/explore-page-data";
import {
  hasLoggedInHint,
  readAnonJobLanguagesPreference,
} from "@/lib/client-cookies";
import { logExternalError } from "@/lib/safe-external-error";
import { hasSearchFilterParams } from "@/lib/search/query-params";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { useSession } from "@/components/providers/SessionProvider";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { loadExploreBrowserData } from "@/lib/search/explore-browser-data";
import { SearchPage } from "./search-page";

type ExploreContentProps = {
  locale: string;
  /**
   * Server-prerendered ``ExploreData`` for the unauthenticated, no-filter
   * homepage case (#2640). Anonymous visitors with no filter searchParams
   * use this directly — no Vercel function invocation. When ``initialData``
   * The cached route always supplies this query-agnostic shell. Preference-
   * and filter-bearing views replace it from browser-direct Typesense reads.
   */
  initialData: ExploreData;
};

export function ExploreContent({ locale, initialData }: ExploreContentProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const fetchIdRef = useRef(0);
  const loadKeyRef = useRef<string | null>(null);
  const { isLoggedIn, isPending, preferences } = useSession();
  const rates = useSalaryRates();
  const preferenceLanguagesKey = preferences?.jobLanguages?.join(",") ?? "";
  const preferenceCurrency = preferences?.displayCurrency ?? null;
  const [view, setView] = useState<{
    data: ExploreData;
    unavailable: boolean;
    directAttempted: boolean;
  } | null>({
    data: initialData,
    unavailable: false,
    directAttempted: false,
  });

  // Re-initialize only when the query-agnostic shell does not reflect the
  // browser URL or viewer preferences. Authenticated preferences come from
  // the shared app bootstrap action; anonymous language state is bounded and
  // parsed from its client-readable cookie. Result data and explicit taxonomy
  // resolution stay browser-direct, with only semantic free text retaining a
  // narrow geo-aware parser action.
  useEffect(() => {
    // Keep the cached server snapshot visible until this interactive island
    // has hydrated successfully. At that point swap the two atomically: the
    // static representation is hidden from layout and accessibility APIs,
    // while SearchPage takes over filters, dialogs, pagination, and actions.
    const interactive = rootRef.current?.closest<HTMLElement>("[data-explore-interactive]");
    const staticSnapshot = interactive?.previousElementSibling;
    if (staticSnapshot instanceof HTMLElement && staticSnapshot.hasAttribute("data-explore-static-results")) {
      staticSnapshot.setAttribute("hidden", "");
    }
    interactive?.removeAttribute("hidden");

    const searchParams = new URLSearchParams(window.location.search);
    if (hasLoggedInHint() && isPending) {
      // Invalidate any prior viewer-state request while the hinted session is
      // unresolved. The eventual authenticated/anonymous key will start a new
      // request once bootstrap settles.
      fetchIdRef.current += 1;
      setView(null);
      return;
    }

    const anonymousJobLanguages = isLoggedIn
      ? null
      : readAnonJobLanguagesPreference();
    const loadKey = [
      locale,
      searchParams.toString(),
      isLoggedIn ? "authenticated" : "anonymous",
      preferenceCurrency ?? "",
      preferences?.jobLanguages?.join(",") ??
        anonymousJobLanguages?.join(",") ??
        "",
    ].join("|");
    if (loadKeyRef.current === loadKey) return;
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
      setView({
        data: initialData,
        unavailable: false,
        directAttempted: false,
      });
      return;
    }

    // Unmount SearchPage before the browser load: its state is initialized
    // from props, and a filtered URL must never flash or retain the broader
    // queryless shell while its scoped request is pending.
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
    isLoggedIn,
    isPending,
    locale,
    preferenceCurrency,
    preferenceLanguagesKey,
    rates,
  ]);

  if (!view) return <ExploreSkeleton />;
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
    <div ref={rootRef} data-explore-content-root>
      <SearchPage
        initialCompanies={result.companies}
        initialTotalCompanies={result.totalCompanies}
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
