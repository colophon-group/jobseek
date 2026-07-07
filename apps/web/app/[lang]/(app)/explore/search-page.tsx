"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";

import type { SelectedLocation } from "@/lib/search/types";
import { SearchResults } from "@/components/search/search-results";
import { SearchUnavailable } from "@/components/search/search-unavailable";
import { ZeroResults } from "@/components/search/zero-results";
import { SkeletonCards } from "@/components/search/skeleton-card";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { SearchToolbar } from "@/components/search/search-toolbar";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { runSearchJobs, runListTopCompanies } from "@/lib/search/search-runner";
import { useClearTypesenseOnAuthChange } from "@/lib/search/use-clear-typesense-on-auth-change";
import { useSession } from "@/components/providers/SessionProvider";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SearchResultCompany, HistogramFilters, WorkMode } from "@/lib/search";
import {
  useSearchStateStore,
  buildCacheKey,
  shouldRestoreSnapshot,
} from "@/components/providers/SearchStateProvider";

const PAGE_SIZE = 10;

type TaxonomyItem = { id: number; slug: string; name: string };

interface SearchPageProps {
  initialCompanies: SearchResultCompany[];
  initialTotalCompanies: number;
  initialTruncated?: boolean;
  initialDegraded?: boolean;
  initialKeywords: string[];
  initialLocations: SelectedLocation[];
  initialOccupations: TaxonomyItem[];
  initialSeniorities: TaxonomyItem[];
  initialTechnologies: TaxonomyItem[];
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
  userLat?: number;
  userLng?: number;
}

export function SearchPage({
  initialCompanies,
  initialTotalCompanies,
  initialTruncated,
  initialDegraded,
  initialKeywords,
  initialLocations,
  initialOccupations,
  initialSeniorities,
  initialTechnologies,
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
  languages,
  userLat,
  userLng,
}: SearchPageProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { isLoggedIn } = useSession();
  const isLoggedInRef = useRef(isLoggedIn);
  isLoggedInRef.current = isLoggedIn;
  const { get: getSearchState, set: setSearchState, setPageActions } = useSearchStateStore();

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

  const [keywords, setKeywords] = useState<string[]>(
    shouldRestore ? cached.keywords : initialKeywords,
  );
  const [locations, setLocations] = useState<SelectedLocation[]>(
    shouldRestore ? cached.locations : initialLocations,
  );
  const [occupations, setOccupations] = useState<TaxonomyItem[]>(
    shouldRestore ? cached.occupations : initialOccupations,
  );
  const [seniorities, setSeniorities] = useState<TaxonomyItem[]>(
    shouldRestore ? cached.seniorities : initialSeniorities,
  );
  const [technologies, setTechnologies] = useState<TaxonomyItem[]>(
    shouldRestore ? cached.technologies : initialTechnologies,
  );
  const [salaryCurrency, setSalaryCurrency] = useState(
    shouldRestore ? cached.salaryCurrency : (initialSalaryCurrency ?? displayCurrency),
  );
  const [salaryMin, setSalaryMin] = useState<number | undefined>(
    shouldRestore ? cached.salaryMinEur : initialSalaryMin,
  );
  const [salaryMax, setSalaryMax] = useState<number | undefined>(
    shouldRestore ? cached.salaryMaxEur : initialSalaryMax,
  );
  const [experienceMin, setExperienceMin] = useState<number | undefined>(
    shouldRestore ? cached.experienceMin : initialExperienceMin,
  );
  const [experienceMax, setExperienceMax] = useState<number | undefined>(
    shouldRestore ? cached.experienceMax : initialExperienceMax,
  );

  const [employmentTypes, setEmploymentTypes] = useState<string[]>(
    shouldRestore ? cached.employmentTypes ?? [] : initialEmploymentTypes,
  );
  const [workMode, setWorkMode] = useState<WorkMode[]>(
    shouldRestore ? cached.workMode : initialWorkMode,
  );

  // Currency rates for EUR conversion — read from `SalaryDisplayProvider`
  // which fetches once on mount and shares the table with every consumer
  // on this layout (search page, salary modal, salary cells). Previously
  // each consumer fired its own `getCurrencyRates()`, producing 3 server
  // actions per `/explore` view; see #3181.
  const currencyRates = useSalaryRates();

  useClearTypesenseOnAuthChange(isLoggedIn);

  const [showPostingId, setShowPostingId] = useState<string | null>(
    searchParams.get("show") ?? (shouldRestore ? cached.showPostingId : null),
  );
  const [companies, setCompanies] = useState<SearchResultCompany[]>(
    shouldRestore ? cached.companies : initialCompanies,
  );
  const [totalCompanies, setTotalCompanies] = useState(
    shouldRestore ? cached.totalCompanies : initialTotalCompanies,
  );
  const [isSearching, setIsSearching] = useState(false);
  const searchCounterRef = useRef(0);
  const [isTruncated, setIsTruncated] = useState(initialTruncated ?? false);
  const [isDegraded, setIsDegraded] = useState(
    shouldRestore ? (cached.degraded ?? false) : (initialDegraded ?? false),
  );
  // Track server-side offset separately from deduped client list length.
  // Facet-based pagination can return overlapping companies between pages,
  // causing the deduped list to grow slower than the server offset.
  const serverOffsetRef = useRef(initialCompanies.length);

  // Refs for all filter state — single source of truth for updateUrl/runSearch
  const keywordsRef = useRef(keywords);
  const locationsRef = useRef(locations);
  const occupationsRef = useRef(occupations);
  const senioritiesRef = useRef(seniorities);
  const technologiesRef = useRef(technologies);
  const employmentTypesRef = useRef(employmentTypes);
  const workModeRef = useRef(workMode);
  const salaryCurrencyRef = useRef(salaryCurrency);
  const salaryMinRef = useRef(salaryMin);
  const salaryMaxRef = useRef(salaryMax);
  const experienceMinRef = useRef(experienceMin);
  const experienceMaxRef = useRef(experienceMax);
  const companiesRef = useRef(companies);
  const totalCompaniesRef = useRef(totalCompanies);
  const showPostingIdRef = useRef(showPostingId);
  const isDegradedRef = useRef(isDegraded);
  keywordsRef.current = keywords;
  locationsRef.current = locations;
  occupationsRef.current = occupations;
  senioritiesRef.current = seniorities;
  technologiesRef.current = technologies;
  employmentTypesRef.current = employmentTypes;
  workModeRef.current = workMode;
  salaryCurrencyRef.current = salaryCurrency;
  salaryMinRef.current = salaryMin;
  salaryMaxRef.current = salaryMax;
  experienceMinRef.current = experienceMin;
  experienceMaxRef.current = experienceMax;
  companiesRef.current = companies;
  totalCompaniesRef.current = totalCompanies;
  showPostingIdRef.current = showPostingId;
  isDegradedRef.current = isDegraded;

  // Flag to distinguish our own URL changes (replaceState) from external
  // navigation (router.push from header search bar, back/forward, etc.)
  const internalUrlChangeRef = useRef(false);

  // Build a search-only key from params, excluding UI-only params like "show".
  function buildSearchKey(sp: URLSearchParams): string {
    const filtered = new URLSearchParams();
    sp.forEach((v, k) => { if (k !== "show") filtered.set(k, v); });
    return filtered.toString();
  }

  // Track the last search key we've processed so we only react to genuine
  // external URL changes — not mount, StrictMode double-runs, or our own
  // replaceState calls.
  const lastSearchKeyRef = useRef(buildSearchKey(searchParams));

  // Detect external URL changes (e.g. header search bar → router.push)
  // and re-parse filters + search, without remounting the component.
  useEffect(() => {
    const currentKey = buildSearchKey(searchParams);
    if (internalUrlChangeRef.current) {
      internalUrlChangeRef.current = false;
      lastSearchKeyRef.current = currentKey;
      return; // our own replaceState — already handled by runSearch
    }
    if (currentKey === lastSearchKeyRef.current) {
      return; // same params — mount, StrictMode double-run, or no-op
    }
    lastSearchKeyRef.current = currentKey;

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

    const parseSalParts = sal ? sal.split("-") : [];
    const newSalMin = parseSalParts[0] ? parseInt(parseSalParts[0], 10) : undefined;
    const newSalMax = parseSalParts[1] ? parseInt(parseSalParts[1], 10) : undefined;
    const parseExpParts = exp ? exp.split("-") : [];
    const newExpMin = parseExpParts[0] ? parseInt(parseExpParts[0], 10) : undefined;
    const newExpMax = parseExpParts[1] ? parseInt(parseExpParts[1], 10) : undefined;
    if (salcur) { setSalaryCurrency(salcur); salaryCurrencyRef.current = salcur; }
    if (newSalMin !== undefined || newSalMax !== undefined) {
      setSalaryMin(newSalMin); salaryMinRef.current = newSalMin;
      setSalaryMax(newSalMax); salaryMaxRef.current = newSalMax;
    }
    if (newExpMin !== undefined || newExpMax !== undefined) {
      setExperienceMin(newExpMin); experienceMinRef.current = newExpMin;
      setExperienceMax(newExpMax); experienceMaxRef.current = newExpMax;
    }

    setIsSearching(true);
    parseSearchFilters({ q, loc, occ, sen, tech, wm, etype, locale, userLat, userLng }).then((parsed) => {
      setKeywords(parsed.keywords); keywordsRef.current = parsed.keywords;
      setLocations(parsed.locations); locationsRef.current = parsed.locations;
      setOccupations(parsed.occupations); occupationsRef.current = parsed.occupations;
      setSeniorities(parsed.seniorities); senioritiesRef.current = parsed.seniorities;
      setTechnologies(parsed.technologies); technologiesRef.current = parsed.technologies;
      setEmploymentTypes(parsed.employmentTypes); employmentTypesRef.current = parsed.employmentTypes;
      setWorkMode(parsed.workMode); workModeRef.current = parsed.workMode;
      runSearch();
    });
  }, [searchParams]);

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
          },
        ),
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
        locationsRef.current = updated;
        updateUrl();
        runSearch();
      },
      addOccupation: (occ) => {
        const updated = [...occupationsRef.current, occ];
        setOccupations(updated);
        occupationsRef.current = updated;
        updateUrl();
        runSearch();
      },
      addSeniority: (sen) => {
        const updated = [...senioritiesRef.current, sen];
        setSeniorities(updated);
        senioritiesRef.current = updated;
        updateUrl();
        runSearch();
      },
      addTechnology: (tech) => {
        const updated = [...technologiesRef.current, tech];
        setTechnologies(updated);
        technologiesRef.current = updated;
        updateUrl();
        runSearch();
      },
      submitSearch: (nextKeywords, nextLocations, nextOccupations, nextSeniorities, nextTechnologies) => {
        setKeywords(nextKeywords); keywordsRef.current = nextKeywords;
        setLocations(nextLocations); locationsRef.current = nextLocations;
        if (nextOccupations) { setOccupations(nextOccupations); occupationsRef.current = nextOccupations; }
        if (nextSeniorities) { setSeniorities(nextSeniorities); senioritiesRef.current = nextSeniorities; }
        if (nextTechnologies) { setTechnologies(nextTechnologies); technologiesRef.current = nextTechnologies; }
        setShowPostingId(null); showPostingIdRef.current = null;
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
        employmentTypesRef.current = updated;
        updateUrl();
        runSearch();
      },
      addWorkMode: (mode) => {
        if (workModeRef.current.includes(mode)) return;
        const updated = [...workModeRef.current, mode];
        setWorkMode(updated);
        workModeRef.current = updated;
        updateUrl();
        runSearch();
      },
      setSalaryFilter: (currency: string, min: number | undefined, max: number | undefined) => {
        setSalaryCurrency(currency); salaryCurrencyRef.current = currency;
        setSalaryMin(min); salaryMinRef.current = min;
        setSalaryMax(max); salaryMaxRef.current = max;
        updateUrl();
        runSearch();
      },
      setExperienceFilter: (min: number | undefined, max: number | undefined) => {
        setExperienceMin(min); experienceMinRef.current = min;
        setExperienceMax(max); experienceMaxRef.current = max;
        updateUrl();
        runSearch();
      },
    });
  }, [setPageActions]);

  // Restore scroll position and sync URL on mount when restoring from cache
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
  const hasFilters = keywords.length > 0 || locations.length > 0 || occupations.length > 0 || seniorities.length > 0 || technologies.length > 0 || employmentTypes.length > 0 || workMode.length > 0 || salaryMin != null || salaryMax != null || experienceMin != null || experienceMax != null;

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
                  languages,
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
                  languages,
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
      } catch {
        // Keep existing results visible on error
      } finally {
        if (searchCounterRef.current === id) setIsSearching(false);
      }
    })();
  };
  function runSearch() { runSearchRef.current(); }

  const handleRemoveKeyword = useCallback(
    (keyword: string) => {
      const updated = keywordsRef.current.filter((k) => k !== keyword);
      setKeywords(updated);
      keywordsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddLocation = useCallback(
    (location: SelectedLocation) => {
      const updated = [...locationsRef.current, location];
      setLocations(updated);
      locationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddOccupation = useCallback(
    (occ: TaxonomyItem) => {
      const updated = [...occupationsRef.current, occ];
      setOccupations(updated);
      occupationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddSeniority = useCallback(
    (sen: TaxonomyItem) => {
      const updated = [...senioritiesRef.current, sen];
      setSeniorities(updated);
      senioritiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleSubmitSearch = useCallback(
    (nextKeywords: string[], nextLocations: SelectedLocation[], nextOccs?: TaxonomyItem[], nextSens?: TaxonomyItem[], nextTechs?: TaxonomyItem[]) => {
      setKeywords(nextKeywords); keywordsRef.current = nextKeywords;
      setLocations(nextLocations); locationsRef.current = nextLocations;
      if (nextOccs) { setOccupations(nextOccs); occupationsRef.current = nextOccs; }
      if (nextSens) { setSeniorities(nextSens); senioritiesRef.current = nextSens; }
      if (nextTechs) { setTechnologies(nextTechs); technologiesRef.current = nextTechs; }
      setShowPostingId(null); showPostingIdRef.current = null;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveLocation = useCallback(
    (locationId: number) => {
      const updated = locationsRef.current.filter((l) => l.id !== locationId);
      setLocations(updated);
      locationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveOccupation = useCallback(
    (occId: number) => {
      const updated = occupationsRef.current.filter((o) => o.id !== occId);
      setOccupations(updated);
      occupationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveSeniority = useCallback(
    (senId: number) => {
      const updated = senioritiesRef.current.filter((s) => s.id !== senId);
      setSeniorities(updated);
      senioritiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleAddTechnology = useCallback(
    (tech: TaxonomyItem) => {
      const updated = [...technologiesRef.current, tech];
      setTechnologies(updated);
      technologiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleRemoveTechnology = useCallback(
    (techId: number) => {
      const updated = technologiesRef.current.filter((t) => t.id !== techId);
      setTechnologies(updated);
      technologiesRef.current = updated;
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
      salaryCurrencyRef.current = currency;
      salaryMinRef.current = min;
      salaryMaxRef.current = max;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleExperienceChange = useCallback(
    (min: number | undefined, max: number | undefined) => {
      setExperienceMin(min);
      setExperienceMax(max);
      experienceMinRef.current = min;
      experienceMaxRef.current = max;
      updateUrl();
      runSearch();
    },
    [],
  );

  const handleClearAll = useCallback(() => {
    setKeywords([]); keywordsRef.current = [];
    setLocations([]); locationsRef.current = [];
    setOccupations([]); occupationsRef.current = [];
    setSeniorities([]); senioritiesRef.current = [];
    setTechnologies([]); technologiesRef.current = [];
    setEmploymentTypes([]); employmentTypesRef.current = [];
    setWorkMode([]); workModeRef.current = [];
    setSalaryCurrency(displayCurrency); salaryCurrencyRef.current = displayCurrency;
    setSalaryMin(undefined); salaryMinRef.current = undefined;
    setSalaryMax(undefined); salaryMaxRef.current = undefined;
    setExperienceMin(undefined); experienceMinRef.current = undefined;
    setExperienceMax(undefined); experienceMaxRef.current = undefined;
    setShowPostingId(null); showPostingIdRef.current = null;
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
      ? await runSearchJobs({ keywords: kws, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes: etypes, workMode: wm, salaryMinEur: salMinEur, salaryMaxEur: salMaxEur, experienceMin: expMin, experienceMax: expMax, languages, locale, offset, limit: PAGE_SIZE }, isLoggedInRef.current)
      : await runListTopCompanies({ locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes: etypes, workMode: wm, salaryMinEur: salMinEur, salaryMaxEur: salMaxEur, experienceMin: expMin, experienceMax: expMax, languages, locale, offset, limit: PAGE_SIZE }, isLoggedInRef.current);

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
        salaryCurrency={salaryCurrency}
        salaryMin={salaryMin}
        salaryMax={salaryMax}
        experienceMin={experienceMin}
        experienceMax={experienceMax}
        jobLanguages={jobLanguages}
        onRemoveKeyword={handleRemoveKeyword}
        onAddLocation={handleAddLocation}
        onRemoveLocation={handleRemoveLocation}
        onAddOccupation={handleAddOccupation}
        onRemoveOccupation={handleRemoveOccupation}
        onAddSeniority={handleAddSeniority}
        onRemoveSeniority={handleRemoveSeniority}
        onAddTechnology={handleAddTechnology}
        onRemoveTechnology={handleRemoveTechnology}
        employmentTypes={employmentTypes}
        onToggleEmploymentType={(type) => {
          const exists = employmentTypesRef.current.includes(type);
          const updated = exists ? employmentTypesRef.current.filter((t) => t !== type) : [...employmentTypesRef.current, type];
          setEmploymentTypes(updated);
          employmentTypesRef.current = updated;
          updateUrl();
          runSearch();
        }}
        workMode={workMode}
        onToggleWorkMode={(mode) => {
          const exists = workModeRef.current.includes(mode);
          const updated = exists ? workModeRef.current.filter((m) => m !== mode) : [...workModeRef.current, mode];
          setWorkMode(updated);
          workModeRef.current = updated;
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
      ) : showUnavailable ? (
        <SearchUnavailable />
      ) : companies.length === 0 && hasFilters ? (
        <ZeroResults query={[...keywords, ...locations.map((l) => l.name)].join(", ")} />
      ) : (
        <div className={isSearching ? "opacity-60 pointer-events-none transition-opacity" : ""}>
          <SearchResults
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
          {/* On small screens, show as an overlay */}
          <div className="fixed inset-0 z-50 bg-black/40 lg:hidden" onClick={handleClosePosting}>
            <div
              className="absolute inset-y-0 right-0 w-full max-w-lg bg-surface shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
