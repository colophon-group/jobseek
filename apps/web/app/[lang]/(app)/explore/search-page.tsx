"use client";

import { useState, useCallback, useTransition, useRef, useEffect, useMemo } from "react";
import { usePathname, useSearchParams } from "next/navigation";

import type { SelectedLocation } from "@/components/search/location-pills";
import { SearchResults } from "@/components/search/search-results";
import { ZeroResults } from "@/components/search/zero-results";
import { SkeletonCards } from "@/components/search/skeleton-card";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { SearchToolbar } from "@/components/search/search-toolbar";
import { searchJobs, listTopCompanies, getCurrencyRates, type CurrencyRate } from "@/lib/actions/search";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SearchResultCompany, HistogramFilters } from "@/lib/search";
import {
  useSearchStateStore,
  buildCacheKey,
} from "@/components/SearchStateProvider";

const PAGE_SIZE = 10;

type TaxonomyItem = { id: number; slug: string; name: string };

interface SearchPageProps {
  initialCompanies: SearchResultCompany[];
  initialTotalCompanies: number;
  initialTruncated?: boolean;
  initialKeywords: string[];
  initialLocations: SelectedLocation[];
  initialOccupations: TaxonomyItem[];
  initialSeniorities: TaxonomyItem[];
  initialTechnologies: TaxonomyItem[];
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
  initialKeywords,
  initialLocations,
  initialOccupations,
  initialSeniorities,
  initialTechnologies,
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
  const { get: getSearchState, set: setSearchState, setPageActions } = useSearchStateStore();

  const cached = getSearchState();
  const currentCacheKey = buildCacheKey(
    initialKeywords,
    initialLocations.map((l) => l.id),
    initialOccupations.map((o) => o.id),
    initialSeniorities.map((s) => s.id),
    initialTechnologies.map((t) => t.id),
  );
  const hasUrlFilters = initialKeywords.length > 0 || initialLocations.length > 0 || initialOccupations.length > 0 || initialSeniorities.length > 0 || initialTechnologies.length > 0;
  const shouldRestore =
    cached !== null &&
    (cached.cacheKey === currentCacheKey || !hasUrlFilters);

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

  const [employmentTypes, setEmploymentTypes] = useState<string[]>([]);

  // Currency rates for EUR conversion (fetched lazily)
  const [currencyRates, setCurrencyRates] = useState<CurrencyRate[]>([]);
  useEffect(() => {
    getCurrencyRates().then(setCurrencyRates);
  }, []);

  const [showPostingId, setShowPostingId] = useState<string | null>(
    searchParams.get("show") ?? (shouldRestore ? cached.showPostingId : null),
  );
  const [companies, setCompanies] = useState<SearchResultCompany[]>(
    shouldRestore ? cached.companies : initialCompanies,
  );
  const [totalCompanies, setTotalCompanies] = useState(
    shouldRestore ? cached.totalCompanies : initialTotalCompanies,
  );
  const [isSearching, startSearch] = useTransition();
  const [isTruncated, setIsTruncated] = useState(initialTruncated ?? false);

  // Refs for all filter state — single source of truth for updateUrl/runSearch
  const keywordsRef = useRef(keywords);
  const locationsRef = useRef(locations);
  const occupationsRef = useRef(occupations);
  const senioritiesRef = useRef(seniorities);
  const technologiesRef = useRef(technologies);
  const employmentTypesRef = useRef(employmentTypes);
  const salaryCurrencyRef = useRef(salaryCurrency);
  const salaryMinRef = useRef(salaryMin);
  const salaryMaxRef = useRef(salaryMax);
  const experienceMinRef = useRef(experienceMin);
  const experienceMaxRef = useRef(experienceMax);
  const companiesRef = useRef(companies);
  const totalCompaniesRef = useRef(totalCompanies);
  const showPostingIdRef = useRef(showPostingId);
  keywordsRef.current = keywords;
  locationsRef.current = locations;
  occupationsRef.current = occupations;
  senioritiesRef.current = seniorities;
  technologiesRef.current = technologies;
  employmentTypesRef.current = employmentTypes;
  salaryCurrencyRef.current = salaryCurrency;
  salaryMinRef.current = salaryMin;
  salaryMaxRef.current = salaryMax;
  experienceMinRef.current = experienceMin;
  experienceMaxRef.current = experienceMax;
  companiesRef.current = companies;
  totalCompaniesRef.current = totalCompanies;
  showPostingIdRef.current = showPostingId;

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
        salaryMinEur: salaryMinRef.current,
        salaryMaxEur: salaryMaxRef.current,
        salaryCurrency: salaryCurrencyRef.current,
        experienceMin: experienceMinRef.current,
        experienceMax: experienceMaxRef.current,
        companies: companiesRef.current,
        totalCompanies: totalCompaniesRef.current,
        showPostingId: showPostingIdRef.current,
        scrollY: window.scrollY,
        cacheKey: buildCacheKey(
          keywordsRef.current,
          locationsRef.current.map((l) => l.id),
          occupationsRef.current.map((o) => o.id),
          senioritiesRef.current.map((s) => s.id),
          technologiesRef.current.map((t) => t.id),
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
      const url = buildFilteredPath(
        pathname,
        cached.keywords,
        cached.locations,
        cached.showPostingId ? { show: cached.showPostingId } : undefined,
        cached.occupations,
        cached.seniorities,
        cached.technologies,
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
  const hasFilters = keywords.length > 0 || locations.length > 0 || occupations.length > 0 || seniorities.length > 0 || technologies.length > 0 || employmentTypes.length > 0 || salaryMin != null || salaryMax != null || experienceMin != null || experienceMax != null;

  /** Update only the `show` query param without touching filter state. */
  function updateShowParam(postingId: string | null) {
    const url = new URL(window.location.href);
    if (postingId) {
      url.searchParams.set("show", postingId);
    } else {
      url.searchParams.delete("show");
    }
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  /** Sync URL to current filter state. */
  function updateUrl() {
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
    const url = buildFilteredPath(
      pathname,
      keywordsRef.current,
      locationsRef.current,
      Object.keys(extra).length > 0 ? extra : undefined,
      occupationsRef.current,
      senioritiesRef.current,
      technologiesRef.current,
    );
    window.history.replaceState(null, "", url);
  }

  function handleOpenPosting(postingId: string) {
    setShowPostingId(postingId);
    updateShowParam(postingId);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    updateShowParam(null);
  }

  /** Run a search using current ref state. */
  function runSearch() {
    const kws = keywordsRef.current;
    const locationIds = locationsRef.current.map((l) => l.id);
    const occupationIds = occupationsRef.current.map((o) => o.id);
    const seniorityIds = senioritiesRef.current.map((s) => s.id);
    const technologyIds = technologiesRef.current.map((t) => t.id);
    const etypes = employmentTypesRef.current;
    const salMinEur = toEur(salaryMinRef.current);
    const salMaxEur = toEur(salaryMaxRef.current);
    const expMin = experienceMinRef.current;
    const expMax = experienceMaxRef.current;
    startSearch(async () => {
      try {
        const result =
          kws.length > 0
            ? await searchJobs({
                keywords: kws,
                locationIds,
                occupationIds: occupationIds.length > 0 ? occupationIds : undefined,
                seniorityIds: seniorityIds.length > 0 ? seniorityIds : undefined,
                technologyIds: technologyIds.length > 0 ? technologyIds : undefined,
                employmentTypes: etypes.length > 0 ? etypes : undefined,
                salaryMinEur: salMinEur,
                salaryMaxEur: salMaxEur,
                experienceMin: expMin,
                experienceMax: expMax,
                languages,
                locale,
                offset: 0,
                limit: PAGE_SIZE,
              })
            : await listTopCompanies({
                locationIds,
                occupationIds: occupationIds.length > 0 ? occupationIds : undefined,
                seniorityIds: seniorityIds.length > 0 ? seniorityIds : undefined,
                technologyIds: technologyIds.length > 0 ? technologyIds : undefined,
                employmentTypes: etypes.length > 0 ? etypes : undefined,
                salaryMinEur: salMinEur,
                salaryMaxEur: salMaxEur,
                experienceMin: expMin,
                experienceMax: expMax,
                languages,
                locale,
                offset: 0,
                limit: PAGE_SIZE,
              });
        setCompanies(result.companies);
        setTotalCompanies(result.totalCompanies);
        setIsTruncated(result.truncated ?? false);
      } catch {
        // Ensure transition ends even on error — keeps existing results visible
      }
    });
  }

  const handleRemoveKeyword = useCallback(
    (keyword: string) => {
      const updated = keywords.filter((k) => k !== keyword);
      setKeywords(updated);
      keywordsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [keywords],
  );

  const handleAddLocation = useCallback(
    (location: SelectedLocation) => {
      const updated = [...locations, location];
      setLocations(updated);
      locationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [locations],
  );

  const handleAddOccupation = useCallback(
    (occ: TaxonomyItem) => {
      const updated = [...occupations, occ];
      setOccupations(updated);
      occupationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [occupations],
  );

  const handleAddSeniority = useCallback(
    (sen: TaxonomyItem) => {
      const updated = [...seniorities, sen];
      setSeniorities(updated);
      senioritiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [seniorities],
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
      const updated = locations.filter((l) => l.id !== locationId);
      setLocations(updated);
      locationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [locations],
  );

  const handleRemoveOccupation = useCallback(
    (occId: number) => {
      const updated = occupations.filter((o) => o.id !== occId);
      setOccupations(updated);
      occupationsRef.current = updated;
      updateUrl();
      runSearch();
    },
    [occupations],
  );

  const handleRemoveSeniority = useCallback(
    (senId: number) => {
      const updated = seniorities.filter((s) => s.id !== senId);
      setSeniorities(updated);
      senioritiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [seniorities],
  );

  const handleAddTechnology = useCallback(
    (tech: TaxonomyItem) => {
      const updated = [...technologies, tech];
      setTechnologies(updated);
      technologiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [technologies],
  );

  const handleRemoveTechnology = useCallback(
    (techId: number) => {
      const updated = technologies.filter((t) => t.id !== techId);
      setTechnologies(updated);
      technologiesRef.current = updated;
      updateUrl();
      runSearch();
    },
    [technologies],
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
    const offset = companiesRef.current.length;
    const kws = keywordsRef.current;
    const locationIds = locationsRef.current.map((l) => l.id);
    const occupationIds = occupationsRef.current.length > 0 ? occupationsRef.current.map((o) => o.id) : undefined;
    const seniorityIds = senioritiesRef.current.length > 0 ? senioritiesRef.current.map((s) => s.id) : undefined;
    const technologyIds = technologiesRef.current.length > 0 ? technologiesRef.current.map((t) => t.id) : undefined;
    const etypes = employmentTypesRef.current.length > 0 ? employmentTypesRef.current : undefined;
    const salMinEur = toEur(salaryMinRef.current);
    const salMaxEur = toEur(salaryMaxRef.current);
    const expMin = experienceMinRef.current;
    const expMax = experienceMaxRef.current;
    const result = kws.length > 0
      ? await searchJobs({ keywords: kws, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes: etypes, salaryMinEur: salMinEur, salaryMaxEur: salMaxEur, experienceMin: expMin, experienceMax: expMax, languages, locale, offset, limit: PAGE_SIZE })
      : await listTopCompanies({ locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes: etypes, salaryMinEur: salMinEur, salaryMaxEur: salMaxEur, experienceMin: expMin, experienceMax: expMax, languages, locale, offset, limit: PAGE_SIZE });

    if (result.truncated) setIsTruncated(true);

    setCompanies((prev) => {
      const seen = new Set(prev.map((c) => c.company.id));
      return [...prev, ...result.companies.filter((c) => !seen.has(c.company.id))];
    });
    setTotalCompanies(result.totalCompanies);
  }

  const histogramFilters: HistogramFilters = useMemo(() => ({
    keywords: keywords.length > 0 ? keywords : undefined,
    locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
    occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
    seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
    technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
    languages: languages.length > 0 ? languages : undefined,
  }), [keywords, locations, occupations, seniorities, technologies, languages]);

  const searchColumn = (
    <div className="space-y-6">
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
        onSalaryChange={handleSalaryChange}
        onExperienceChange={handleExperienceChange}
        histogramFilters={histogramFilters}
        onClearAll={handleClearAll}
        onSubmitSearch={handleSubmitSearch}
      />

      {isSearching ? (
        <SkeletonCards count={3} />
      ) : companies.length === 0 && hasFilters ? (
        <ZeroResults query={[...keywords, ...locations.map((l) => l.name)].join(", ")} />
      ) : (
        <SearchResults
          companies={companies}
          keywords={keywords}
          locationIds={locations.map((l) => l.id)}
          locations={locations}
          occupations={occupations}
          seniorities={seniorities}
          technologies={technologies}
          employmentTypes={employmentTypes}
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
