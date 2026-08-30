"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { Trans } from "@lingui/react/macro";

import type { SelectedLocation } from "@/lib/search/types";
import { SearchResults } from "@/components/search/search-results";
import { SearchUnavailable } from "@/components/search/search-unavailable";
import { ExploreRepositoryFallback } from "@/components/search/explore-repository-fallback";
import { ZeroResults } from "@/components/search/zero-results";
import { SkeletonCards } from "@/components/search/skeleton-card";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { MobileJobDetailDialog } from "@/components/search/mobile-job-detail-dialog";
import { SearchToolbar } from "@/components/search/search-toolbar";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import {
  runSearchJobs,
  runListTopCompanies,
  tryListTopCompaniesDirect,
} from "@/lib/search/search-runner";
import { useSession } from "@/components/providers/SessionProvider";
import { fetchExploreFilterPageData } from "@/lib/actions/explore-page-data";
import type { ParsedSearchFilters } from "@/lib/actions/search-input";
import { buildFilteredPath } from "@/lib/search/query-params";
import { parseExploreSearchLanguages } from "@/lib/search/language-param";
import { parseOfflineSearchFilters } from "@/lib/search/offline-filters";
import { resolveJobLanguages } from "@/lib/job-languages";
import { useLatest, useLatestState } from "@/lib/use-latest";
import { useBrowserSearchParams } from "@/lib/use-browser-search-params";
import type { SearchResultCompany, HistogramFilters, WorkMode } from "@/lib/search";
import type { ExploreRepositoryCompany } from "@/lib/explore-repository-fallback";
import {
  useSearchStateStore,
  buildCacheKey,
  shouldRestoreSnapshot,
} from "@/components/providers/SearchStateProvider";

const PAGE_SIZE = 10;

type TaxonomyItem = { id: number; slug: string; name: string };
type UnresolvedExplicitSlugs = NonNullable<
  ParsedSearchFilters["unresolvedExplicitSlugs"]
>;

function hasUnresolvedExplicitSlugs(value: UnresolvedExplicitSlugs): boolean {
  return (["loc", "occ", "sen", "tech"] as const).some(
    (kind) => (value[kind]?.length ?? 0) > 0,
  );
}

function mergedFilterSlugs(
  resolved: Array<{ slug: string }>,
  unresolved: string[] | undefined,
): string {
  const seen = new Set<string>();
  return [...resolved.map((item) => item.slug), ...(unresolved ?? [])]
    .filter((slug) => {
      const key = slug.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .join(",");
}

export function resolveInitialRepositoryFallbackCompanies(params: {
  shouldRestore: boolean;
  cachedCompaniesLength: number;
  cachedDegraded: boolean;
  initialCompanies: ExploreRepositoryCompany[] | undefined;
}): ExploreRepositoryCompany[] {
  const { shouldRestore, cachedCompaniesLength, cachedDegraded, initialCompanies } = params;
  if (!shouldRestore) return initialCompanies ?? [];
  // A matching filtered snapshot can legitimately restore with no live
  // companies. When that snapshot records the same degraded offline state,
  // retain the only available profile links across the profile-link/back
  // navigation. Never reintroduce them over restored live or non-degraded
  // zero-result data.
  return cachedCompaniesLength === 0 && cachedDegraded
    ? (initialCompanies ?? [])
    : [];
}

interface SearchPageProps {
  initialCompanies: SearchResultCompany[];
  initialTotalCompanies: number;
  initialTruncated?: boolean;
  initialDegraded?: boolean;
  initialRepositoryFallbackCompanies?: ExploreRepositoryCompany[];
  initialKeywords: string[];
  initialLocations: SelectedLocation[];
  initialOccupations: TaxonomyItem[];
  initialSeniorities: TaxonomyItem[];
  initialTechnologies: TaxonomyItem[];
  initialUnresolvedExplicitSlugs?: UnresolvedExplicitSlugs;
  initialEmploymentTypes: string[];
  initialWorkMode: WorkMode[];
  initialSalaryCurrency?: string;
  initialSalaryMin?: number;
  initialSalaryMax?: number;
  initialExperienceMin?: number;
  initialExperienceMax?: number;
  locale: string;
  displayCurrency: string;
  /** Raw preference: [] = default, ["*"] = all, ["en","de"] = specific */
  jobLanguages: string[];
  /** Resolved language filter for search queries */
  languages: string[];
  /** URL-level override supplied by the public API's `moreAt` link. */
  initialLanguageOverride?: string[] | null;
  userLat?: number;
  userLng?: number;
}

export function SearchPage({
  initialCompanies,
  initialTotalCompanies,
  initialTruncated,
  initialDegraded,
  initialRepositoryFallbackCompanies,
  initialKeywords,
  initialLocations,
  initialOccupations,
  initialSeniorities,
  initialTechnologies,
  initialUnresolvedExplicitSlugs,
  initialEmploymentTypes,
  initialWorkMode,
  initialSalaryCurrency,
  initialSalaryMin,
  initialSalaryMax,
  initialExperienceMin,
  initialExperienceMax,
  locale,
  displayCurrency,
  jobLanguages,
  languages: initialLanguages,
  initialLanguageOverride,
  userLat,
  userLng,
}: SearchPageProps) {
  // The cached route is always `/<locale>/explore`; query state is observed
  // separately after hydration. Reading `usePathname()` here would suspend
  // the result-owning subtree during prerender and replace its company cards
  // with the route loading skeleton, the same failure mode as
  // `useSearchParams()` in #2640.
  const pathname = `/${locale}/explore`;
  const searchParams = useBrowserSearchParams();
  const { isLoggedIn } = useSession();
  const isLoggedInRef = useLatest(isLoggedIn);
  const { get: getSearchState, set: setSearchState, setPageActions } = useSearchStateStore();
  const [languageOverride, setLanguageOverride, languageOverrideRef] =
    useLatestState<string[] | null>(initialLanguageOverride ?? null);
  const [languages, setLanguages, languagesRef] =
    useLatestState<string[]>(initialLanguages);

  const cachedSnapshot = getSearchState();
  const currentCacheKey = buildCacheKey(
    initialKeywords,
    initialLocations.map((l) => l.id),
    initialOccupations.map((o) => o.id),
    initialSeniorities.map((s) => s.id),
    initialTechnologies.map((t) => t.id),
    {
      employmentTypes: initialEmploymentTypes,
      workMode: initialWorkMode,
      salaryMin: initialSalaryMin,
      salaryMax: initialSalaryMax,
      salaryCurrency: initialSalaryMin != null || initialSalaryMax != null
        ? initialSalaryCurrency ?? displayCurrency
        : undefined,
      experienceMin: initialExperienceMin,
      experienceMax: initialExperienceMax,
      languages,
      unresolvedExplicitSlugs: initialUnresolvedExplicitSlugs,
    },
  );
  // Restore the cached snapshot only when it matches the current URL
  // filters exactly. Without the strict match, a snapshot saved from a
  // previous filtered search (e.g. an empty-result query for
  // "rare-keyword") would leak its ``keywords`` and empty
  // ``companies`` into a fresh ``/explore`` visit, surfacing
  // ``ZeroResults`` despite the URL having no filters. See #2989.
  const cached = shouldRestoreSnapshot(cachedSnapshot, currentCacheKey)
    ? cachedSnapshot
    : null;
  const shouldRestore = cached !== null;

  const [keywords, setKeywords, keywordsRef] = useLatestState<string[]>(
    shouldRestore ? cached.keywords : initialKeywords,
  );
  const [locations, setLocations, locationsRef] = useLatestState<SelectedLocation[]>(
    shouldRestore ? cached.locations : initialLocations,
  );
  const [occupations, setOccupations, occupationsRef] = useLatestState<TaxonomyItem[]>(
    shouldRestore ? cached.occupations : initialOccupations,
  );
  const [seniorities, setSeniorities, senioritiesRef] = useLatestState<TaxonomyItem[]>(
    shouldRestore ? cached.seniorities : initialSeniorities,
  );
  const [technologies, setTechnologies, technologiesRef] = useLatestState<TaxonomyItem[]>(
    shouldRestore ? cached.technologies : initialTechnologies,
  );
  const [unresolvedExplicitSlugs, setUnresolvedExplicitSlugs, unresolvedExplicitSlugsRef] =
    useLatestState<UnresolvedExplicitSlugs>(
      shouldRestore
        ? (cached.unresolvedExplicitSlugs ?? {})
        : (initialUnresolvedExplicitSlugs ?? {}),
    );
  const [salaryCurrency, setSalaryCurrency, salaryCurrencyRef] = useLatestState(
    shouldRestore ? cached.salaryCurrency : (initialSalaryCurrency ?? displayCurrency),
  );
  const [salaryMin, setSalaryMin, salaryMinRef] = useLatestState<number | undefined>(
    shouldRestore ? cached.salaryMinEur : initialSalaryMin,
  );
  const [salaryMax, setSalaryMax, salaryMaxRef] = useLatestState<number | undefined>(
    shouldRestore ? cached.salaryMaxEur : initialSalaryMax,
  );
  const [experienceMin, setExperienceMin, experienceMinRef] = useLatestState<number | undefined>(
    shouldRestore ? cached.experienceMin : initialExperienceMin,
  );
  const [experienceMax, setExperienceMax, experienceMaxRef] = useLatestState<number | undefined>(
    shouldRestore ? cached.experienceMax : initialExperienceMax,
  );

  const [employmentTypes, setEmploymentTypes, employmentTypesRef] = useLatestState<string[]>(
    shouldRestore ? cached.employmentTypes ?? [] : initialEmploymentTypes,
  );
  const [workMode, setWorkMode, workModeRef] = useLatestState<WorkMode[]>(
    shouldRestore ? cached.workMode : initialWorkMode,
  );

  // Currency rates for EUR conversion — read from `SalaryDisplayProvider`
  // which fetches once on mount and shares the table with every consumer
  // on this layout (search page, salary modal, salary cells). Previously
  // each consumer fired its own `getCurrencyRates()`, producing 3 server
  // actions per `/explore` view; see #3181.
  const currencyRates = useSalaryRates();

  const [showPostingId, setShowPostingId, showPostingIdRef] = useLatestState<string | null>(
    shouldRestore ? cached.showPostingId : null,
  );
  const [companies, setCompanies, companiesRef] = useLatestState<SearchResultCompany[]>(
    shouldRestore ? cached.companies : initialCompanies,
  );
  const [totalCompanies, setTotalCompanies, totalCompaniesRef] = useLatestState(
    shouldRestore ? cached.totalCompanies : initialTotalCompanies,
  );
  const [isSearching, setIsSearching] = useState(false);
  const searchCounterRef = useRef(0);
  const externalNavigationCounterRef = useRef(0);
  const initialDirectRefreshKeyRef = useRef<string | null>(null);
  const [isTruncated, setIsTruncated] = useState(initialTruncated ?? false);
  const [isDegraded, setIsDegraded, isDegradedRef] = useLatestState(
    shouldRestore ? (cached.degraded ?? false) : (initialDegraded ?? false),
  );
  const [repositoryFallbackCompanies, setRepositoryFallbackCompanies] = useState(
    resolveInitialRepositoryFallbackCompanies({
      shouldRestore,
      cachedCompaniesLength: cached?.companies.length ?? 0,
      cachedDegraded: cached?.degraded ?? false,
      initialCompanies: initialRepositoryFallbackCompanies,
    }),
  );
  // Track server-side offset separately from deduped client list length.
  // Facet-based pagination can return overlapping companies between pages,
  // causing the deduped list to grow slower than the server offset.
  const serverOffsetRef = useRef(initialCompanies.length);

  // Latest-state refs are the single source of truth for stable
  // updateUrl/runSearch/pageActions callbacks.

  // Flag to distinguish our own URL changes (replaceState) from external
  // navigation (router.push from header search bar, back/forward, etc.)
  const internalUrlChangeRef = useRef(false);

  // Build a search-only key from params, excluding UI-only params like "show".
  function buildSearchKey(sp: URLSearchParams): string {
    const filtered = new URLSearchParams();
    sp.forEach((v, k) => { if (k !== "show") filtered.set(k, v); });
    return filtered.toString();
  }

  function buildExternalSearchKey(sp: URLSearchParams): string {
    return [
      locale,
      userLat ?? "",
      userLng ?? "",
      buildSearchKey(sp),
    ].join("|");
  }

  // Track the last search key we've processed so we only react to genuine
  // external URL changes — not mount, StrictMode double-runs, or our own
  // replaceState calls.
  // Ignore the first observed browser snapshot and use the committed URL as
  // the baseline. During hydration the hook first exposes its queryless server
  // snapshot; during cross-route App Router navigation the initial render can
  // still see the source route until Next's HistoryUpdater commits the target
  // in an insertion effect. ExploreContent owns the destination's one-time
  // filtered/personalized fetch, so neither mount shape should trigger a
  // second SearchPage request.
  const isBrowserUrlReadyRef = useRef(false);
  const lastSearchKeyRef = useRef("");

  // Detect external URL changes (e.g. header search bar → router.push)
  // and re-parse filters + search, without remounting the component.
  useEffect(() => {
    const currentKey = buildExternalSearchKey(searchParams);
    if (!isBrowserUrlReadyRef.current) {
      const committedKey = buildExternalSearchKey(
        new URLSearchParams(window.location.search),
      );
      lastSearchKeyRef.current = committedKey;
      // Stay in baseline mode while hydration or Next's cross-route
      // HistoryUpdater still exposes the stale/source snapshot. This also
      // survives StrictMode's repeated mount effects because readiness only
      // flips when the subscribed snapshot matches the committed address bar.
      if (currentKey === committedKey) {
        isBrowserUrlReadyRef.current = true;
      }
      return;
    }

    if (internalUrlChangeRef.current) {
      internalUrlChangeRef.current = false;
      // Filter-changing History API writes already started their own search,
      // so invalidate only an older taxonomy parse. A `show`-only write has
      // the same result key and must not cancel the pending navigation.
      if (currentKey !== lastSearchKeyRef.current) {
        externalNavigationCounterRef.current += 1;
      }
      lastSearchKeyRef.current = currentKey;
      return; // our own replaceState — already handled by runSearch
    }
    if (currentKey === lastSearchKeyRef.current) {
      return; // same params — mount, StrictMode double-run, or no-op
    }
    lastSearchKeyRef.current = currentKey;
    const navigationId = ++externalNavigationCounterRef.current;
    // Cancel ownership of any search started for the previous address before
    // awaiting taxonomy resolution for this one.
    searchCounterRef.current += 1;

    // External navigation: parse URL params and update state
    const q = searchParams.get("q") ?? undefined;
    const loc = searchParams.get("loc") ?? undefined;
    const occ = searchParams.get("occ") ?? undefined;
    const sen = searchParams.get("sen") ?? undefined;
    const tech = searchParams.get("tech") ?? undefined;
    const wm = searchParams.get("wm") ?? undefined;
    const etype = searchParams.get("etype") ?? undefined;
    const sal = searchParams.get("sal") ?? undefined;
    const salcur = searchParams.get("salcur") ?? undefined;
    const exp = searchParams.get("exp") ?? undefined;
    const parsedLanguageOverride = parseExploreSearchLanguages(
      searchParams.get("lang"),
    );
    const nextLanguageOverride = parsedLanguageOverride.ok
      ? parsedLanguageOverride.languages
      : null;
    setLanguageOverride(nextLanguageOverride);
    setLanguages(
      nextLanguageOverride ?? resolveJobLanguages(jobLanguages, locale),
    );

    const parseSalParts = sal ? sal.split("-") : [];
    const newSalMin = parseSalParts[0] ? parseInt(parseSalParts[0], 10) : undefined;
    const newSalMax = parseSalParts[1] ? parseInt(parseSalParts[1], 10) : undefined;
    const parseExpParts = exp ? exp.split("-") : [];
    const newExpMin = parseExpParts[0] ? parseInt(parseExpParts[0], 10) : undefined;
    const newExpMax = parseExpParts[1] ? parseInt(parseExpParts[1], 10) : undefined;
    setSalaryCurrency(salcur ?? displayCurrency);
    setSalaryMin(newSalMin);
    setSalaryMax(newSalMax);
    setExperienceMin(newExpMin);
    setExperienceMax(newExpMax);

    setIsSearching(true);
    fetchExploreFilterPageData({ q, loc, occ, sen, tech, wm, etype, locale, userLat, userLng })
      .then(({ parsed, degraded }) => {
        if (externalNavigationCounterRef.current !== navigationId) return;
        setKeywords(parsed.keywords);
        setLocations(parsed.locations);
        setOccupations(parsed.occupations);
        setSeniorities(parsed.seniorities);
        setTechnologies(parsed.technologies);
        setUnresolvedExplicitSlugs(parsed.unresolvedExplicitSlugs ?? {});
        setEmploymentTypes(parsed.employmentTypes);
        setWorkMode(parsed.workMode);
        if (degraded || hasUnresolvedExplicitSlugs(parsed.unresolvedExplicitSlugs ?? {})) {
          // Do not run a broader search after losing explicit taxonomy IDs.
          // The URL still carries the submitted slugs, while the result area
          // clearly distinguishes upstream unavailability from zero matches.
          setCompanies([]);
          setTotalCompanies(0);
          serverOffsetRef.current = 0;
          setIsTruncated(false);
          setIsDegraded(true);
          setRepositoryFallbackCompanies([]);
          setIsSearching(false);
          return;
        }
        runSearch();
      })
      .catch(() => {
        if (externalNavigationCounterRef.current !== navigationId) return;
        // The browser URL already moved. Keeping the previous companies here
        // would put stale/broader results beneath the new filter state. Parse
        // everything that is safe offline and show explicit unavailability.
        const parsed = parseOfflineSearchFilters({ q, loc, occ, sen, tech, wm, etype });
        setKeywords(parsed.keywords);
        setLocations([]);
        setOccupations([]);
        setSeniorities([]);
        setTechnologies([]);
        setUnresolvedExplicitSlugs(parsed.unresolvedExplicitSlugs ?? {});
        setEmploymentTypes(parsed.employmentTypes);
        setWorkMode(parsed.workMode);
        setCompanies([]);
        setTotalCompanies(0);
        serverOffsetRef.current = 0;
        setIsTruncated(false);
        setIsDegraded(true);
        setRepositoryFallbackCompanies([]);
        setIsSearching(false);
      });
  }, [searchParams, locale, userLat, userLng]);

  useEffect(() => () => {
    externalNavigationCounterRef.current += 1;
    searchCounterRef.current += 1;
  }, []);

  // `show` is UI-only and does not participate in the result search key.
  // Keep it synchronized for external query navigation while preserving the
  // queryless server snapshot used for hydration-safe raw HTML.
  useEffect(() => {
    setShowPostingId(new URLSearchParams(window.location.search).get("show"));
  }, [searchParams, setShowPostingId]);

  /** Convert a salary amount from the user's display currency to EUR. */
  function toEur(amount: number | undefined): number | undefined {
    if (amount == null) return undefined;
    const rate = currencyRates.find((r) => r.currency === salaryCurrencyRef.current);
    if (!rate) return amount; // fallback: assume EUR
    return Math.round(amount * rate.toEur);
  }

  // Save state to context on unmount
  useEffect(() => {
    return () => {
      setSearchState({
        keywords: keywordsRef.current,
        locations: locationsRef.current,
        occupations: occupationsRef.current,
        seniorities: senioritiesRef.current,
        technologies: technologiesRef.current,
        unresolvedExplicitSlugs: unresolvedExplicitSlugsRef.current,
        employmentTypes: employmentTypesRef.current,
        workMode: workModeRef.current,
        salaryMinEur: salaryMinRef.current,
        salaryMaxEur: salaryMaxRef.current,
        salaryCurrency: salaryCurrencyRef.current,
        experienceMin: experienceMinRef.current,
        experienceMax: experienceMaxRef.current,
        companies: companiesRef.current,
        totalCompanies: totalCompaniesRef.current,
        showPostingId: showPostingIdRef.current,
        degraded: isDegradedRef.current,
        scrollY: window.scrollY,
        cacheKey: buildCacheKey(
          keywordsRef.current,
          locationsRef.current.map((l) => l.id),
          occupationsRef.current.map((o) => o.id),
          senioritiesRef.current.map((s) => s.id),
          technologiesRef.current.map((t) => t.id),
          {
            employmentTypes: employmentTypesRef.current,
            workMode: workModeRef.current,
            salaryMin: salaryMinRef.current,
            salaryMax: salaryMaxRef.current,
            salaryCurrency:
              salaryMinRef.current != null || salaryMaxRef.current != null
                ? salaryCurrencyRef.current
                : undefined,
            experienceMin: experienceMinRef.current,
            experienceMax: experienceMaxRef.current,
            languages: languagesRef.current,
            unresolvedExplicitSlugs: unresolvedExplicitSlugsRef.current,
          },
        ),
        hasResultFilters:
          keywordsRef.current.length > 0 ||
          locationsRef.current.length > 0 ||
          occupationsRef.current.length > 0 ||
          senioritiesRef.current.length > 0 ||
          technologiesRef.current.length > 0 ||
          hasUnresolvedExplicitSlugs(unresolvedExplicitSlugsRef.current) ||
          employmentTypesRef.current.length > 0 ||
          workModeRef.current.length > 0 ||
          salaryMinRef.current != null ||
          salaryMaxRef.current != null ||
          experienceMinRef.current != null ||
          experienceMaxRef.current != null ||
          (languageOverrideRef.current?.length ?? 0) > 0,
      });
      setPageActions(null);
    };
  }, [setSearchState, setPageActions]);

  // Register live actions so the header SearchBar can interact directly
  useEffect(() => {
    setPageActions({
      addLocation: (loc) => {
        const updated = [...locationsRef.current, loc];
        setLocations(updated);
        markExplicitSlugsResolved({ loc: updated });

        updateUrl();
        runSearch();
      },
      addOccupation: (occ) => {
        const updated = [...occupationsRef.current, occ];
        setOccupations(updated);
        markExplicitSlugsResolved({ occ: updated });

        updateUrl();
        runSearch();
      },
      addSeniority: (sen) => {
        const updated = [...senioritiesRef.current, sen];
        setSeniorities(updated);
        markExplicitSlugsResolved({ sen: updated });

        updateUrl();
        runSearch();
      },
      addTechnology: (tech) => {
        const updated = [...technologiesRef.current, tech];
        setTechnologies(updated);
        markExplicitSlugsResolved({ tech: updated });

        updateUrl();
        runSearch();
      },
      submitSearch: (nextKeywords, nextLocations, nextOccupations, nextSeniorities, nextTechnologies, nextWorkMode) => {
        setKeywords(nextKeywords);
        setLocations(nextLocations);
        if (nextOccupations) { setOccupations(nextOccupations); }
        if (nextSeniorities) { setSeniorities(nextSeniorities); }
        if (nextTechnologies) { setTechnologies(nextTechnologies); }
        if (nextWorkMode) { setWorkMode(nextWorkMode); }
        markExplicitSlugsResolved({
          loc: nextLocations,
          ...(nextOccupations ? { occ: nextOccupations } : {}),
          ...(nextSeniorities ? { sen: nextSeniorities } : {}),
          ...(nextTechnologies ? { tech: nextTechnologies } : {}),
        });
        setShowPostingId(null);
        updateUrl();
        runSearch();
      },
      getLocations: () => locationsRef.current,
      getKeywords: () => keywordsRef.current,
      getOccupations: () => occupationsRef.current,
      getSeniorities: () => senioritiesRef.current,
      getTechnologies: () => technologiesRef.current,
      addEmploymentType: (type: string) => {
        if (employmentTypesRef.current.includes(type)) return;
        const updated = [...employmentTypesRef.current, type];
        setEmploymentTypes(updated);

        updateUrl();
        runSearch();
      },
      addWorkMode: (mode) => {
        if (workModeRef.current.includes(mode)) return;
        const updated = [...workModeRef.current, mode];
        setWorkMode(updated);

        updateUrl();
        runSearch();
      },
      setSalaryFilter: (currency: string, min: number | undefined, max: number | undefined) => {
        setSalaryCurrency(currency);
        setSalaryMin(min);
        setSalaryMax(max);
        updateUrl();
        runSearch();
      },
      setExperienceFilter: (min: number | undefined, max: number | undefined) => {
        setExperienceMin(min);
        setExperienceMax(max);
        updateUrl();
        runSearch();
      },
    });
  }, [setPageActions]);

  // Restore scroll position and sync URL on mount when restoring from cache.
  // This is intentionally snapshot-only: re-running after the user edits
  // filters would overwrite the live URL and scroll state with stale cache.
  useEffect(() => {
    if (shouldRestore) {
      const extra: Record<string, string> = {};
      if (cached.showPostingId) extra.show = cached.showPostingId;
      if (cached.employmentTypes?.length) extra.etype = cached.employmentTypes.join(",");
      if (cached.salaryMinEur != null || cached.salaryMaxEur != null) {
        extra.sal = `${cached.salaryMinEur ?? ""}-${cached.salaryMaxEur ?? ""}`;
      }
      if (cached.salaryCurrency && cached.salaryCurrency !== displayCurrency) {
        extra.salcur = cached.salaryCurrency;
      }
      if (cached.experienceMin != null || cached.experienceMax != null) {
        extra.exp = `${cached.experienceMin ?? ""}-${cached.experienceMax ?? ""}`;
      }
      if (languageOverrideRef.current !== null) {
        extra.lang = languageOverrideRef.current.join(",") || "*";
      }
      const unresolved = cached.unresolvedExplicitSlugs ?? {};
      const locationSlugs = mergedFilterSlugs(cached.locations, unresolved.loc);
      const occupationSlugs = mergedFilterSlugs(cached.occupations, unresolved.occ);
      const senioritySlugs = mergedFilterSlugs(cached.seniorities, unresolved.sen);
      const technologySlugs = mergedFilterSlugs(cached.technologies, unresolved.tech);
      if (locationSlugs) extra.loc = locationSlugs;
      if (occupationSlugs) extra.occ = occupationSlugs;
      if (senioritySlugs) extra.sen = senioritySlugs;
      if (technologySlugs) extra.tech = technologySlugs;
      const url = buildFilteredPath(
        pathname,
        cached.keywords,
        cached.locations,
        Object.keys(extra).length > 0 ? extra : undefined,
        cached.occupations,
        cached.seniorities,
        cached.technologies,
        cached.workMode,
      );
      window.history.replaceState(null, "", url);

      if (cached.scrollY > 0) {
        requestAnimationFrame(() => {
          window.scrollTo(0, cached.scrollY);
        });
      }
    }
  }, []);

  const hasMore = companies.length < totalCompanies && !isTruncated;
  const hasFilters = keywords.length > 0 || locations.length > 0 || occupations.length > 0 || seniorities.length > 0 || technologies.length > 0 || hasUnresolvedExplicitSlugs(unresolvedExplicitSlugs) || employmentTypes.length > 0 || workMode.length > 0 || salaryMin != null || salaryMax != null || experienceMin != null || experienceMax != null || (languageOverride?.length ?? 0) > 0;

  /** Update only the `show` query param without touching filter state. */
  function updateShowParam(postingId: string | null) {
    internalUrlChangeRef.current = true;
    const url = new URL(window.location.href);
    if (postingId) {
      url.searchParams.set("show", postingId);
    } else {
      url.searchParams.delete("show");
    }
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  /** Sync URL to current filter state. */
  const updateUrlRef = useRef(() => {});
  updateUrlRef.current = () => {
    const extra: Record<string, string> = {};
    if (showPostingIdRef.current) extra.show = showPostingIdRef.current;
    if (salaryMinRef.current || salaryMaxRef.current) {
      extra.sal = `${salaryMinRef.current ?? ""}-${salaryMaxRef.current ?? ""}`;
    }
    if (salaryCurrencyRef.current && salaryCurrencyRef.current !== "EUR") {
      extra.salcur = salaryCurrencyRef.current;
    }
    if (experienceMinRef.current || experienceMaxRef.current) {
      extra.exp = `${experienceMinRef.current ?? ""}-${experienceMaxRef.current ?? ""}`;
    }
    if (employmentTypesRef.current.length > 0) {
      extra.etype = employmentTypesRef.current.join(",");
    }
    if (languageOverrideRef.current !== null) {
      extra.lang = languageOverrideRef.current.join(",") || "*";
    }
    const unresolved = unresolvedExplicitSlugsRef.current;
    const locationSlugs = mergedFilterSlugs(locationsRef.current, unresolved.loc);
    const occupationSlugs = mergedFilterSlugs(occupationsRef.current, unresolved.occ);
    const senioritySlugs = mergedFilterSlugs(senioritiesRef.current, unresolved.sen);
    const technologySlugs = mergedFilterSlugs(technologiesRef.current, unresolved.tech);
    if (locationSlugs) extra.loc = locationSlugs;
    if (occupationSlugs) extra.occ = occupationSlugs;
    if (senioritySlugs) extra.sen = senioritySlugs;
    if (technologySlugs) extra.tech = technologySlugs;
    const url = buildFilteredPath(
      pathname,
      keywordsRef.current,
      locationsRef.current,
      Object.keys(extra).length > 0 ? extra : undefined,
      occupationsRef.current,
      senioritiesRef.current,
      technologiesRef.current,
      workModeRef.current,
    );
    window.history.replaceState(null, "", url);
  };
  function updateUrl() { internalUrlChangeRef.current = true; updateUrlRef.current(); }

  function markExplicitSlugsResolved(
    resolvedByKind: Partial<Record<keyof UnresolvedExplicitSlugs, Array<{ slug: string }>>>,
  ) {
    const current = unresolvedExplicitSlugsRef.current;
    let changed = false;
    const next: UnresolvedExplicitSlugs = { ...current };
    for (const kind of ["loc", "occ", "sen", "tech"] as const) {
      const resolved = resolvedByKind[kind];
      const unresolved = current[kind];
      if (!resolved || !unresolved?.length) continue;
      const resolvedKeys = new Set(resolved.map((item) => item.slug.toLowerCase()));
      const remaining = unresolved.filter(
        (slug) => !resolvedKeys.has(slug.toLowerCase()),
      );
      if (remaining.length === unresolved.length) continue;
      changed = true;
      if (remaining.length > 0) next[kind] = remaining;
      else delete next[kind];
    }
    if (changed) setUnresolvedExplicitSlugs(next);
  }

  const handleRemoveUnresolvedSlug = useCallback(
    (kind: keyof UnresolvedExplicitSlugs, slug: string) => {
      const current = unresolvedExplicitSlugsRef.current;
      const remaining = (current[kind] ?? []).filter(
        (value) => value.toLowerCase() !== slug.toLowerCase(),
      );
      const next: UnresolvedExplicitSlugs = { ...current };
      if (remaining.length > 0) next[kind] = remaining;
      else delete next[kind];
      setUnresolvedExplicitSlugs(next);

      updateUrl();
      runSearch();
    },
    [],
  );

  // Stabilized for #3198 — passed into `SearchResults` -> `CompanyCard`
  // which is wrapped in `React.memo` with a custom comparator that
  // checks `onShowPosting` by reference. Without `useCallback`, every
  // parent render hands every card a new function and the memo is
  // a no-op. `setShowPostingId` / `updateShowParam` are stable
  // (state setter + module-scoped function reading refs), so an empty
  // dep array is correct here.
  const handleOpenPosting = useCallback((postingId: string) => {
    setShowPostingId(postingId);
    updateShowParam(postingId);
  }, []);

  function handleClosePosting() {
    setShowPostingId(null);
    updateShowParam(null);
  }

  /** Run a search using current ref state. */
  const runSearchRef = useRef(() => {});
  runSearchRef.current = () => {
    if (hasUnresolvedExplicitSlugs(unresolvedExplicitSlugsRef.current)) {
      searchCounterRef.current += 1;
      setCompanies([]);
      setTotalCompanies(0);
      setIsTruncated(false);
      setIsDegraded(true);
      setIsSearching(false);
      return;
    }
    const kws = keywordsRef.current;
    const locationIds = locationsRef.current.map((l) => l.id);
    const occupationIds = occupationsRef.current.map((o) => o.id);
    const seniorityIds = senioritiesRef.current.map((s) => s.id);
    const technologyIds = technologiesRef.current.map((t) => t.id);
    const etypes = employmentTypesRef.current;
    const wm = workModeRef.current;
    const salMinEur = toEur(salaryMinRef.current);
    const salMaxEur = toEur(salaryMaxRef.current);
    const expMin = experienceMinRef.current;
    const expMax = experienceMaxRef.current;
    const id = ++searchCounterRef.current;
    setIsSearching(true);
    (async () => {
      try {
        const result =
          kws.length > 0
            ? await runSearchJobs(
                {
                  keywords: kws,
                  locationIds,
                  occupationIds: occupationIds.length > 0 ? occupationIds : undefined,
                  seniorityIds: seniorityIds.length > 0 ? seniorityIds : undefined,
                  technologyIds: technologyIds.length > 0 ? technologyIds : undefined,
                  employmentTypes: etypes.length > 0 ? etypes : undefined,
                  workMode: wm.length > 0 ? wm : undefined,
                  salaryMinEur: salMinEur,
                  salaryMaxEur: salMaxEur,
                  experienceMin: expMin,
                  experienceMax: expMax,
                  languages: languagesRef.current,
                  locale,
                  offset: 0,
                  limit: PAGE_SIZE,
                },
                isLoggedInRef.current,
              )
            : await runListTopCompanies(
                {
                  locationIds,
                  occupationIds: occupationIds.length > 0 ? occupationIds : undefined,
                  seniorityIds: seniorityIds.length > 0 ? seniorityIds : undefined,
                  technologyIds: technologyIds.length > 0 ? technologyIds : undefined,
                  employmentTypes: etypes.length > 0 ? etypes : undefined,
                  workMode: wm.length > 0 ? wm : undefined,
                  salaryMinEur: salMinEur,
                  salaryMaxEur: salMaxEur,
                  experienceMin: expMin,
                  experienceMax: expMax,
                  languages: languagesRef.current,
                  locale,
                  offset: 0,
                  limit: PAGE_SIZE,
                },
                isLoggedInRef.current,
              );
        if (searchCounterRef.current !== id) return; // stale
        setCompanies(result.companies);
        serverOffsetRef.current = result.companies.length;
        setTotalCompanies(result.totalCompanies);
        setIsTruncated(result.truncated ?? false);
        setIsDegraded(result.degraded ?? false);
        if (!result.degraded) setRepositoryFallbackCompanies([]);
      } catch {
        if (searchCounterRef.current !== id) return;
        // The URL/filter controls already changed. Never retain a previous,
        // broader result set under the new state when its search action fails.
        setCompanies([]);
        setTotalCompanies(0);
        serverOffsetRef.current = 0;
        setIsTruncated(false);
        setIsDegraded(true);
        setRepositoryFallbackCompanies([]);
      } finally {
        if (searchCounterRef.current === id) setIsSearching(false);
      }
    })();
  };
  function runSearch() { runSearchRef.current(); }

  // The server payload is deliberately cached longer to avoid continuous ISR
  // regeneration. Refresh the default inventory from browser-direct Typesense
  // after hydration, without a Server Action fallback, so visitors still see
  // current results and the refresh cannot add Fluid CPU.
  const directRefreshLanguagesKey = languages.join(",");
  useEffect(() => {
    if (shouldRestore || hasFilters) return;

    const refreshKey = [
      locale,
      directRefreshLanguagesKey,
      userLat ?? "",
      userLng ?? "",
    ].join("|");
    if (initialDirectRefreshKeyRef.current === refreshKey) return;
    initialDirectRefreshKeyRef.current = refreshKey;

    const id = ++searchCounterRef.current;
    void tryListTopCompaniesDirect(
      {
        locationIds: undefined,
        occupationIds: undefined,
        seniorityIds: undefined,
        technologyIds: undefined,
        employmentTypes: undefined,
        workMode: undefined,
        salaryMinEur: undefined,
        salaryMaxEur: undefined,
        experienceMin: undefined,
        experienceMax: undefined,
        languages,
        locale,
        offset: 0,
        limit: PAGE_SIZE,
      },
      isLoggedInRef.current,
    ).then((result) => {
      if (!result || searchCounterRef.current !== id) return;
      setCompanies(result.companies);
      serverOffsetRef.current = result.companies.length;
      setTotalCompanies(result.totalCompanies);
      setIsTruncated(result.truncated ?? false);
      setIsDegraded(false);
      setRepositoryFallbackCompanies([]);
    });

    return () => {
      if (searchCounterRef.current === id) searchCounterRef.current += 1;
    };
  }, [
    directRefreshLanguagesKey,
    hasFilters,
    locale,
    shouldRestore,
    userLat,
    userLng,
  ]);

  const handleRemoveKeyword = useCallback(
    (keyword: string) => {
      const updated = keywordsRef.current.filter((k) => k !== keyword);
      setKeywords(updated);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddLocation = useCallback(
    (location: SelectedLocation) => {
      const updated = [...locationsRef.current, location];
      setLocations(updated);
      markExplicitSlugsResolved({ loc: updated });

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddOccupation = useCallback(
    (occ: TaxonomyItem) => {
      const updated = [...occupationsRef.current, occ];
      setOccupations(updated);
      markExplicitSlugsResolved({ occ: updated });

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddSeniority = useCallback(
    (sen: TaxonomyItem) => {
      const updated = [...senioritiesRef.current, sen];
      setSeniorities(updated);
      markExplicitSlugsResolved({ sen: updated });

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleSubmitSearch = useCallback(
    (nextKeywords: string[], nextLocations: SelectedLocation[], nextOccs?: TaxonomyItem[], nextSens?: TaxonomyItem[], nextTechs?: TaxonomyItem[]) => {
      setKeywords(nextKeywords);
      setLocations(nextLocations);
      if (nextOccs) { setOccupations(nextOccs); }
      if (nextSens) { setSeniorities(nextSens); }
      if (nextTechs) { setTechnologies(nextTechs); }
      markExplicitSlugsResolved({
        loc: nextLocations,
        ...(nextOccs ? { occ: nextOccs } : {}),
        ...(nextSens ? { sen: nextSens } : {}),
        ...(nextTechs ? { tech: nextTechs } : {}),
      });
      setShowPostingId(null);
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveLocation = useCallback(
    (locationId: number) => {
      const updated = locationsRef.current.filter((l) => l.id !== locationId);
      setLocations(updated);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveOccupation = useCallback(
    (occId: number) => {
      const updated = occupationsRef.current.filter((o) => o.id !== occId);
      setOccupations(updated);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveSeniority = useCallback(
    (senId: number) => {
      const updated = senioritiesRef.current.filter((s) => s.id !== senId);
      setSeniorities(updated);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddTechnology = useCallback(
    (tech: TaxonomyItem) => {
      const updated = [...technologiesRef.current, tech];
      setTechnologies(updated);
      markExplicitSlugsResolved({ tech: updated });

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveTechnology = useCallback(
    (techId: number) => {
      const updated = technologiesRef.current.filter((t) => t.id !== techId);
      setTechnologies(updated);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleSalaryChange = useCallback(
    (currency: string, min: number | undefined, max: number | undefined) => {
      setSalaryCurrency(currency);
      setSalaryMin(min);
      setSalaryMax(max);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleExperienceChange = useCallback(
    (min: number | undefined, max: number | undefined) => {
      setExperienceMin(min);
      setExperienceMax(max);

      updateUrl();
      runSearch();
    },
    [],
  );

  const handleClearAll = useCallback(() => {
    setKeywords([]);
    setLocations([]);
    setOccupations([]);
    setSeniorities([]);
    setTechnologies([]);
    setUnresolvedExplicitSlugs({});
    setEmploymentTypes([]);
    setWorkMode([]);
    setSalaryCurrency(displayCurrency);
    setSalaryMin(undefined);
    setSalaryMax(undefined);
    setExperienceMin(undefined);
    setExperienceMax(undefined);
    setLanguageOverride(null);
    setLanguages(resolveJobLanguages(jobLanguages, locale));
    setShowPostingId(null);
    updateUrl();
    runSearch();
  }, [displayCurrency]);

  async function handleLoadMore() {
    const offset = serverOffsetRef.current;
    const kws = keywordsRef.current;
    const locationIds = locationsRef.current.map((l) => l.id);
    const occupationIds = occupationsRef.current.length > 0 ? occupationsRef.current.map((o) => o.id) : undefined;
    const seniorityIds = senioritiesRef.current.length > 0 ? senioritiesRef.current.map((s) => s.id) : undefined;
    const technologyIds = technologiesRef.current.length > 0 ? technologiesRef.current.map((t) => t.id) : undefined;
    const etypes = employmentTypesRef.current.length > 0 ? employmentTypesRef.current : undefined;
    const wm = workModeRef.current.length > 0 ? workModeRef.current : undefined;
    const salMinEur = toEur(salaryMinRef.current);
    const salMaxEur = toEur(salaryMaxRef.current);
    const expMin = experienceMinRef.current;
    const expMax = experienceMaxRef.current;
    const result = kws.length > 0
      ? await runSearchJobs({ keywords: kws, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes: etypes, workMode: wm, salaryMinEur: salMinEur, salaryMaxEur: salMaxEur, experienceMin: expMin, experienceMax: expMax, languages: languagesRef.current, locale, offset, limit: PAGE_SIZE }, isLoggedInRef.current)
      : await runListTopCompanies({ locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes: etypes, workMode: wm, salaryMinEur: salMinEur, salaryMaxEur: salMaxEur, experienceMin: expMin, experienceMax: expMax, languages: languagesRef.current, locale, offset, limit: PAGE_SIZE }, isLoggedInRef.current);

    if (result.truncated) setIsTruncated(true);
    if (result.degraded) setIsDegraded(true);
    serverOffsetRef.current += result.companies.length;

    setCompanies((prev) => {
      const seen = new Set(prev.map((c) => c.company.id));
      return [...prev, ...result.companies.filter((c) => !seen.has(c.company.id))];
    });
    setTotalCompanies(result.totalCompanies);
  }

  // Stabilized for #3198 — `locationIds` is fed into `SearchResults` and
  // then into each `CompanyCard`. Inline `locations.map((l) => l.id)` in
  // the JSX rebuilt a fresh array on every render, defeating the custom
  // memo comparator's identity-first short-circuit on the array prop.
  const locationIds = useMemo(() => locations.map((l) => l.id), [locations]);
  const showUnavailable = companies.length === 0 && !isSearching && (isDegraded || !hasFilters);

  const histogramFilters: HistogramFilters = useMemo(() => ({
    keywords: keywords.length > 0 ? keywords : undefined,
    locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
    occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
    seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
    technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
    // #3066 — workMode + employmentTypes flow through so the work-mode and
    // employment-type modals can cross-filter their per-option counts against
    // each other (parity with watchlist-view-page). AdvancedSearchPanel strips
    // the active dimension before passing this object down to the matching
    // modal, so the counts answer "what would I see if I toggled this on".
    workMode: workMode.length > 0 ? workMode : undefined,
    employmentTypes: employmentTypes.length > 0 ? employmentTypes : undefined,
    languages: languages.length > 0 ? languages : undefined,
  }), [keywords, locations, occupations, seniorities, technologies, workMode, employmentTypes, languages]);

  const searchColumn = (
    <div className="space-y-6">
      {/*
        Visually-hidden h1 so screen-reader users have a top-level
        heading to anchor heading-jump navigation. The visual design
        leads with the search toolbar, so the h1 is sr-only. See
        WCAG 1.3.1 / issue #3196.
      */}
      <h1 className="sr-only">
        <Trans id="explore.h1" comment="Hidden page H1 for /explore — screen-reader landmark">
          Explore Jobs
        </Trans>
      </h1>
      <SearchToolbar
        locale={locale}
        userLat={userLat}
        userLng={userLng}
        keywords={keywords}
        locations={locations}
        occupations={occupations}
        seniorities={seniorities}
        technologies={technologies}
        unresolvedExplicitSlugs={unresolvedExplicitSlugs}
        salaryCurrency={salaryCurrency}
        salaryMin={salaryMin}
        salaryMax={salaryMax}
        experienceMin={experienceMin}
        experienceMax={experienceMax}
        jobLanguages={
          languageOverride === null
            ? jobLanguages
            : languageOverride.length > 0
              ? languageOverride
              : ["*"]
        }
        onRemoveKeyword={handleRemoveKeyword}
        onAddLocation={handleAddLocation}
        onRemoveLocation={handleRemoveLocation}
        onAddOccupation={handleAddOccupation}
        onRemoveOccupation={handleRemoveOccupation}
        onAddSeniority={handleAddSeniority}
        onRemoveSeniority={handleRemoveSeniority}
        onAddTechnology={handleAddTechnology}
        onRemoveTechnology={handleRemoveTechnology}
        onRemoveUnresolvedSlug={handleRemoveUnresolvedSlug}
        employmentTypes={employmentTypes}
        onToggleEmploymentType={(type) => {
          const exists = employmentTypesRef.current.includes(type);
          const updated = exists ? employmentTypesRef.current.filter((t) => t !== type) : [...employmentTypesRef.current, type];
          setEmploymentTypes(updated);

          updateUrl();
          runSearch();
        }}
        workMode={workMode}
        onToggleWorkMode={(mode) => {
          const exists = workModeRef.current.includes(mode);
          const updated = exists ? workModeRef.current.filter((m) => m !== mode) : [...workModeRef.current, mode];
          setWorkMode(updated);

          updateUrl();
          runSearch();
        }}
        onSalaryChange={handleSalaryChange}
        onExperienceChange={handleExperienceChange}
        histogramFilters={histogramFilters}
        onClearAll={handleClearAll}
        onSubmitSearch={handleSubmitSearch}
      />

      {companies.length === 0 && isSearching ? (
        <SkeletonCards count={3} />
      ) : repositoryFallbackCompanies.length > 0 ? (
        <ExploreRepositoryFallback
          locale={locale}
          companies={repositoryFallbackCompanies}
        />
      ) : showUnavailable ? (
        <SearchUnavailable />
      ) : companies.length === 0 && hasFilters ? (
        <ZeroResults query={[...keywords, ...locations.map((l) => l.name)].join(", ")} />
      ) : (
        <div className={isSearching ? "opacity-60 pointer-events-none transition-opacity" : ""}>
          <SearchResults
            locale={locale}
            companies={companies}
            keywords={keywords}
            locationIds={locationIds}
            locations={locations}
            occupations={occupations}
            seniorities={seniorities}
            technologies={technologies}
            employmentTypes={employmentTypes}
            workMode={workMode}
            salaryMinEur={toEur(salaryMin)}
            salaryMaxEur={toEur(salaryMax)}
            experienceMin={experienceMin}
            experienceMax={experienceMax}
            languages={languages}
            hasMore={hasMore}
            truncated={isTruncated}
            load={handleLoadMore}
            onShowPosting={handleOpenPosting}
            selectedPostingId={showPostingId}
          />
        </div>
      )}
    </div>
  );

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{searchColumn}</div>
      {showPostingId && (
        <>
          {/* Spacer reserves flex layout space on desktop */}
          <div className="hidden w-[420px] shrink-0 lg:block" aria-hidden="true" />
          {/* Fixed panel — immune to overscroll / layout shifts */}
          <div
            className="fixed top-[4.5rem] z-40 hidden w-[420px] lg:block"
            style={{ right: "max(1rem, calc((100vw - 1200px) / 2 + 1rem))", height: "calc(100vh - 5.5rem)" }}
          >
            <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
          </div>
          <MobileJobDetailDialog postingId={showPostingId} onClose={handleClosePosting} />
        </>
      )}
    </div>
  );
}
