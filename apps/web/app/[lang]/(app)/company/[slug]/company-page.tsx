"use client";

import { useState, useCallback, useTransition, useEffect, useMemo } from "react";
import { Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useParams, usePathname, useSearchParams } from "next/navigation";
import { timeAgoShort } from "@/lib/time";
import { SaveButton } from "@/components/search/save-button";
import { SearchUnavailable } from "@/components/search/search-unavailable";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { MobileJobDetailDialog } from "@/components/search/mobile-job-detail-dialog";
import { SearchToolbar } from "@/components/search/search-toolbar";
import { runGetCompanyPostings } from "@/lib/search/search-runner";
import { useClearTypesenseOnAuthChange } from "@/lib/search/use-clear-typesense-on-auth-change";
import { useSession } from "@/components/providers/SessionProvider";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import type { CompanyDetail } from "@/lib/actions/company";
import { buildFilteredPath } from "@/lib/search/query-params";
import { useLatest, useLatestState } from "@/lib/use-latest";
import type { SearchResultPosting, HistogramFilters, WorkMode } from "@/lib/search";
import type { SelectedLocation } from "@/lib/search/types";
import { useSearchStateStore } from "@/components/providers/SearchStateProvider";

const PAGE_SIZE = 20;

type TaxonomyItem = { id: number; slug: string; name: string };

interface CompanyPageProps {
  company: CompanyDetail;
  initialPostings: SearchResultPosting[];
  initialActiveCount: number;
  initialYearCount: number;
  initialTruncated?: boolean;
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
  initialShowPostingId: string | null;
  displayCurrency: string;
  locale: string;
  /** Raw preference: [] = default, ["*"] = all, ["en","de"] = specific */
  jobLanguages: string[];
  /** Resolved language filter for search queries */
  languages: string[];
  userLat?: number;
  userLng?: number;
}

export function CompanyPage({
  company,
  initialPostings,
  initialActiveCount,
  initialYearCount,
  initialTruncated,
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
  initialShowPostingId,
  displayCurrency,
  locale,
  jobLanguages,
  languages,
  userLat,
  userLng,
}: CompanyPageProps) {
  const { t } = useLingui();
  const params = useParams();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const uiLocale = (params.lang as string) ?? locale;
  const { setPageActions } = useSearchStateStore();
  const { isLoggedIn } = useSession();
  const isLoggedInRef = useLatest(isLoggedIn);
  useClearTypesenseOnAuthChange(isLoggedIn);

  const [keywords, setKeywords, keywordsRef] = useLatestState<string[]>(initialKeywords);
  const [locations, setLocations, locationsRef] = useLatestState<SelectedLocation[]>(initialLocations);
  const [occupations, setOccupations, occupationsRef] = useLatestState<TaxonomyItem[]>(initialOccupations);
  const [seniorities, setSeniorities, senioritiesRef] = useLatestState<TaxonomyItem[]>(initialSeniorities);
  const [technologies, setTechnologies, technologiesRef] = useLatestState<TaxonomyItem[]>(initialTechnologies);
  const [salaryCurrency, setSalaryCurrency, salaryCurrencyRef] = useLatestState(initialSalaryCurrency ?? displayCurrency);
  const [salaryMin, setSalaryMin, salaryMinRef] = useLatestState<number | undefined>(initialSalaryMin);
  const [salaryMax, setSalaryMax, salaryMaxRef] = useLatestState<number | undefined>(initialSalaryMax);
  const [experienceMin, setExperienceMin, experienceMinRef] = useLatestState<number | undefined>(initialExperienceMin);
  const [experienceMax, setExperienceMax, experienceMaxRef] = useLatestState<number | undefined>(initialExperienceMax);
  const [employmentTypes, setEmploymentTypes, employmentTypesRef] = useLatestState<string[]>(initialEmploymentTypes);
  const [workMode, setWorkMode, workModeRef] = useLatestState<WorkMode[]>(initialWorkMode);
  const [postings, setPostings] = useState<SearchResultPosting[]>(initialPostings);
  const [activeCount, setActiveCount] = useState(initialActiveCount);
  const [yearCount, setYearCount] = useState(initialYearCount);
  const [showPostingId, setShowPostingId, showPostingIdRef] = useLatestState<string | null>(
    initialShowPostingId ?? searchParams.get("show"),
  );
  const [isSearching, startSearch] = useTransition();
  const [exhausted, setExhausted] = useState(initialPostings.length < PAGE_SIZE);
  const [isTruncated, setIsTruncated] = useState(initialTruncated ?? false);

  // Currency rates for EUR conversion — shared via `SalaryDisplayProvider`
  // which fetches once at the (app) layout root. Previously this page
  // fired a third `getCurrencyRates()` per view (alongside the provider
  // and the salary modal); see #3181.
  const currencyRates = useSalaryRates();

  // Latest-state refs are the single source of truth for stable
  // updateUrl/runSearch/pageActions callbacks.

  const hasMore = !exhausted && !isTruncated && postings.length < yearCount;
  const hasFilters = keywords.length > 0 || locations.length > 0 || occupations.length > 0 || seniorities.length > 0 || technologies.length > 0 || employmentTypes.length > 0 || workMode.length > 0 || salaryMin != null || salaryMax != null || experienceMin != null || experienceMax != null;
  const showUnavailable =
    !isSearching &&
    !hasFilters &&
    postings.length === 0 &&
    (isTruncated || activeCount > 0 || yearCount > 0);

  /** Convert a salary amount from the user's display currency to EUR. */
  function toEur(amount: number | undefined): number | undefined {
    if (amount == null) return undefined;
    const rate = currencyRates.find((r) => r.currency === salaryCurrencyRef.current);
    if (!rate) return amount;
    return Math.round(amount * rate.toEur);
  }

  const searchPlaceholder = t({
    id: "company.page.searchPlaceholder",
    comment: "Placeholder for search bar when on company page",
    message: `Search at ${company.name}...`,
  });

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
  }

  /** Run a search using current ref state. */
  function runSearch() {
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
    startSearch(async () => {
      const result = await runGetCompanyPostings(
        {
          companyId: company.id,
          keywords: kws,
          locationIds: locationIds.length > 0 ? locationIds : undefined,
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
          locale: uiLocale,
          offset: 0,
          limit: PAGE_SIZE,
        },
        isLoggedInRef.current,
      );
      setPostings(result.postings);
      setActiveCount(result.activeCount);
      setYearCount(result.yearCount);
      setExhausted(result.postings.length < PAGE_SIZE);
      setIsTruncated(result.truncated ?? false);
    });
  }

  // Register pageActions so the header SearchBar can interact directly
  useEffect(() => {
    setPageActions({
      addLocation: (loc) => {
        const updated = [...locationsRef.current, loc];
        setLocations(updated);

        updateUrl();
        runSearch();
      },
      addOccupation: (occ) => {
        const updated = [...occupationsRef.current, occ];
        setOccupations(updated);

        updateUrl();
        runSearch();
      },
      addSeniority: (sen) => {
        const updated = [...senioritiesRef.current, sen];
        setSeniorities(updated);

        updateUrl();
        runSearch();
      },
      addTechnology: (tech) => {
        const updated = [...technologiesRef.current, tech];
        setTechnologies(updated);

        updateUrl();
        runSearch();
      },
      submitSearch: (nextKeywords, nextLocations, nextOccupations, nextSeniorities, nextTechnologies) => {
        setKeywords(nextKeywords);
        setLocations(nextLocations);
        if (nextOccupations) { setOccupations(nextOccupations); }
        if (nextSeniorities) { setSeniorities(nextSeniorities); }
        if (nextTechnologies) { setTechnologies(nextTechnologies); }
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
      placeholder: searchPlaceholder,
    });
    return () => setPageActions(null);
  }, [setPageActions, searchPlaceholder]);

  const handleRemoveKeyword = useCallback(
    (keyword: string) => {
      const updated = keywords.filter((k) => k !== keyword);
      setKeywords(updated);

      updateUrl();
      runSearch();
    },
    [keywords],
  );

  const handleAddLocation = useCallback(
    (location: SelectedLocation) => {
      const updated = [...locations, location];
      setLocations(updated);

      updateUrl();
      runSearch();
    },
    [locations],
  );

  const handleRemoveLocation = useCallback(
    (locationId: number) => {
      const updated = locations.filter((l) => l.id !== locationId);
      setLocations(updated);

      updateUrl();
      runSearch();
    },
    [locations],
  );

  const handleAddOccupation = useCallback(
    (occ: TaxonomyItem) => {
      const updated = [...occupations, occ];
      setOccupations(updated);

      updateUrl();
      runSearch();
    },
    [occupations],
  );

  const handleRemoveOccupation = useCallback(
    (occId: number) => {
      const updated = occupations.filter((o) => o.id !== occId);
      setOccupations(updated);

      updateUrl();
      runSearch();
    },
    [occupations],
  );

  const handleAddSeniority = useCallback(
    (sen: TaxonomyItem) => {
      const updated = [...seniorities, sen];
      setSeniorities(updated);

      updateUrl();
      runSearch();
    },
    [seniorities],
  );

  const handleRemoveSeniority = useCallback(
    (senId: number) => {
      const updated = seniorities.filter((s) => s.id !== senId);
      setSeniorities(updated);

      updateUrl();
      runSearch();
    },
    [seniorities],
  );

  const handleAddTechnology = useCallback(
    (tech: TaxonomyItem) => {
      const updated = [...technologies, tech];
      setTechnologies(updated);

      updateUrl();
      runSearch();
    },
    [technologies],
  );

  const handleRemoveTechnology = useCallback(
    (techId: number) => {
      const updated = technologies.filter((t) => t.id !== techId);
      setTechnologies(updated);

      updateUrl();
      runSearch();
    },
    [technologies],
  );

  const handleSubmitSearch = useCallback(
    (nextKeywords: string[], nextLocations: SelectedLocation[], nextOccs?: TaxonomyItem[], nextSens?: TaxonomyItem[], nextTechs?: TaxonomyItem[]) => {
      setKeywords(nextKeywords);
      setLocations(nextLocations);
      if (nextOccs) { setOccupations(nextOccs); }
      if (nextSens) { setSeniorities(nextSens); }
      if (nextTechs) { setTechnologies(nextTechs); }
      setShowPostingId(null);
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
    setEmploymentTypes([]);
    setWorkMode([]);
    setSalaryCurrency(displayCurrency);
    setSalaryMin(undefined);
    setSalaryMax(undefined);
    setExperienceMin(undefined);
    setExperienceMax(undefined);
    setShowPostingId(null);
    updateUrl();
    runSearch();
  }, [displayCurrency]);

  async function handleLoadMore() {
    const locationIds = locations.map((l) => l.id);
    const occupationIds = occupations.length > 0 ? occupations.map((o) => o.id) : undefined;
    const seniorityIds = seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined;
    const technologyIds = technologies.length > 0 ? technologies.map((t) => t.id) : undefined;
    const etypes = employmentTypes.length > 0 ? employmentTypes : undefined;
    const wm = workMode.length > 0 ? workMode : undefined;
    const salMinEur = toEur(salaryMin);
    const salMaxEur = toEur(salaryMax);

    const result = await runGetCompanyPostings(
      {
        companyId: company.id,
        keywords,
        locationIds: locationIds.length > 0 ? locationIds : undefined,
        occupationIds,
        seniorityIds,
        technologyIds,
        employmentTypes: etypes,
        workMode: wm,
        salaryMinEur: salMinEur,
        salaryMaxEur: salMaxEur,
        experienceMin,
        experienceMax,
        languages,
        locale: uiLocale,
        offset: postings.length,
        limit: PAGE_SIZE,
      },
      isLoggedInRef.current,
    );
    if (result.truncated) setIsTruncated(true);
    if (result.postings.length > 0) {
      setPostings((prev) => {
        const seen = new Set(prev.map((p) => p.id));
        return [...prev, ...result.postings.filter((p) => !seen.has(p.id))];
      });
    }
    if (result.postings.length < PAGE_SIZE) {
      setExhausted(true);
    }
  }

  const { sentinelRef, isLoading: isLoadingMore } = useInfiniteScroll({ hasMore, load: handleLoadMore, observerKey: isSearching });

  function handleOpenPosting(postingId: string) {
    setShowPostingId(postingId);
    updateShowParam(postingId);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    updateShowParam(null);
  }

  const histogramFilters: HistogramFilters = useMemo(() => ({
    companyId: company.id,
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
  }), [company.id, keywords, locations, occupations, seniorities, technologies, workMode, employmentTypes, languages]);

  // Desktop: stats sit inline on the language-note row (right side).
  // Hidden on mobile so it can drop to its own row below, split
  // left/right.
  const statsSlot = (
    <p className="hidden whitespace-nowrap text-xs text-muted md:block">
      {activeCount} <Trans id="company.page.active" comment="Active postings count on company page">active</Trans>
      {" · "}
      {yearCount} <Trans id="company.page.yearCount" comment="Year postings count on company page">in the last year</Trans>
    </p>
  );
  // Mobile: dedicated row. Active left, year right.
  const statsRowMobile = (
    <div className="flex items-center justify-between text-xs text-muted md:hidden">
      <span>
        {activeCount}{" "}
        <Trans id="company.page.active" comment="Active postings count on company page">active</Trans>
      </span>
      <span>
        {yearCount}{" "}
        <Trans id="company.page.yearCount" comment="Year postings count on company page">in the last year</Trans>
      </span>
    </div>
  );

  const mainContent = (
    <div className="space-y-4">
      {/* Search toolbar — same as main search page. Stats sit on the
          right of the language-note row via `statsSlot`. */}
      <SearchToolbar
        locale={uiLocale}
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
        searchPlaceholder={searchPlaceholder}
        statsSlot={statsSlot}
      />

      {statsRowMobile}

      {/* Posting list */}
      {isSearching ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 size={20} className="animate-spin text-muted" />
        </div>
      ) : showUnavailable ? (
        <SearchUnavailable />
      ) : postings.length === 0 && hasFilters ? (
        <p className="py-8 text-center text-sm text-muted">
          <Trans id="company.page.noResults" comment="No postings found message on company page">
            No matching postings found.
          </Trans>
        </p>
      ) : (
        <div>
          {postings.map((posting) => (
            <div
              key={posting.id}
              role="button"
              tabIndex={0}
              onClick={() => handleOpenPosting(posting.id)}
              onKeyDown={(e) => { if (e.key === "Enter") handleOpenPosting(posting.id); }}
              className={`flex cursor-pointer items-center gap-2 rounded px-1 py-1.5 transition-colors ${posting.id === showPostingId ? "bg-primary/10" : "hover:bg-border-soft"} ${posting.isActive === false ? "opacity-50" : ""}`}
            >
              <TrackingDot postingId={posting.id} />
              <span className="min-w-0 flex-1 truncate text-sm">{posting.title ?? "—"}</span>
              {posting.isActive === false && (
                <span className="shrink-0 rounded bg-border-soft px-1 py-0.5 text-[10px] text-muted">
                  <Trans id="company.page.closed" comment="Label for inactive/closed job postings on company page">
                    Closed
                  </Trans>
                </span>
              )}
              {posting.locations.length > 0 && (
                <span className={`shrink-0 text-xs text-muted ${posting.locations[0].geoType && posting.locations[0].geoType !== "city" ? "italic" : ""}`}>
                  {posting.locations[0].name}
                  {posting.locations.length > 1 && ` +${posting.locations.length - 1}`}
                </span>
              )}
              {!posting.title && <PendingJobIcon />}
              <SaveButton postingId={posting.id} />
              <span suppressHydrationWarning className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted">
                {timeAgoShort(posting.firstSeenAt)}
              </span>
            </div>
          ))}
          {hasMore && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoadingMore} />}
          {!hasMore && isTruncated && <TruncationPrompt type="postings" />}
        </div>
      )}
    </div>
  );

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{mainContent}</div>
      {showPostingId && (
        <>
          {/* Desktop: side-by-side sticky panel. Matches
              watchlist-job-list.tsx pattern. */}
          <div className="sticky top-[4.5rem] z-40 hidden h-[calc(100vh-5.5rem)] w-[420px] shrink-0 lg:block">
            <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
          </div>
          <MobileJobDetailDialog postingId={showPostingId} onClose={handleClosePosting} />
        </>
      )}
    </div>
  );
}
