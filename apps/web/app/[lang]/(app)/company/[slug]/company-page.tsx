"use client";

import { useState, useCallback, useTransition, useRef, useEffect, useMemo } from "react";
import Image from "next/image";
import { Building2, Loader2 } from "lucide-react";
import { BackLink } from "@/components/BackLink";
import { Trans, useLingui } from "@lingui/react/macro";
import { StarButton } from "@/components/search/star-button";
import { useParams, usePathname, useSearchParams } from "next/navigation";
import { timeAgoShort } from "@/lib/time";
import { SaveButton } from "@/components/search/save-button";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { SearchToolbar } from "@/components/search/search-toolbar";
import { getCompanyPostings } from "@/lib/actions/company";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { getCurrencyRates, type CurrencyRate } from "@/lib/actions/search";
import type { CompanyDetail } from "@/lib/actions/company";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SearchResultPosting, HistogramFilters } from "@/lib/search";
import type { SelectedLocation } from "@/components/search/location-pills";
import { useSearchStateStore } from "@/components/SearchStateProvider";

const PAGE_SIZE = 20;

const EMPLOYEE_RANGE_LABELS: Record<number, string> = {
  1: "1-10",
  2: "11-50",
  3: "51-200",
  4: "201-500",
  5: "501-1,000",
  6: "1,001-5,000",
  7: "5,001-10,000",
  8: "10,000+",
};

type TaxonomyItem = { id: number; slug: string; name: string };

interface CompanyPageProps {
  company: CompanyDetail;
  initialPostings: SearchResultPosting[];
  initialActiveCount: number;
  initialYearCount: number;
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

  const [keywords, setKeywords] = useState<string[]>(initialKeywords);
  const [locations, setLocations] = useState<SelectedLocation[]>(initialLocations);
  const [occupations, setOccupations] = useState<TaxonomyItem[]>(initialOccupations);
  const [seniorities, setSeniorities] = useState<TaxonomyItem[]>(initialSeniorities);
  const [technologies, setTechnologies] = useState<TaxonomyItem[]>(initialTechnologies);
  const [salaryCurrency, setSalaryCurrency] = useState(initialSalaryCurrency ?? displayCurrency);
  const [salaryMin, setSalaryMin] = useState<number | undefined>(initialSalaryMin);
  const [salaryMax, setSalaryMax] = useState<number | undefined>(initialSalaryMax);
  const [experienceMin, setExperienceMin] = useState<number | undefined>(initialExperienceMin);
  const [experienceMax, setExperienceMax] = useState<number | undefined>(initialExperienceMax);
  const [employmentTypes, setEmploymentTypes] = useState<string[]>([]);
  const [postings, setPostings] = useState<SearchResultPosting[]>(initialPostings);
  const [activeCount, setActiveCount] = useState(initialActiveCount);
  const [yearCount, setYearCount] = useState(initialYearCount);
  const [showPostingId, setShowPostingId] = useState<string | null>(
    initialShowPostingId ?? searchParams.get("show"),
  );
  const [isSearching, startSearch] = useTransition();
  const [exhausted, setExhausted] = useState(initialPostings.length < PAGE_SIZE);

  // Currency rates for EUR conversion (fetched lazily)
  const [currencyRates, setCurrencyRates] = useState<CurrencyRate[]>([]);
  useEffect(() => {
    getCurrencyRates().then(setCurrencyRates);
  }, []);

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

  const hasMore = !exhausted && postings.length < yearCount;
  const hasFilters = keywords.length > 0 || locations.length > 0 || occupations.length > 0 || seniorities.length > 0 || technologies.length > 0 || salaryMin != null || salaryMax != null || experienceMin != null || experienceMax != null;

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

  /** Sync URL to current ref state. */
  function updateUrl(showId?: string | null) {
    const extra: Record<string, string> = {};
    if (showId) extra.show = showId;
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
      const result = await getCompanyPostings({
        companyId: company.id,
        keywords: kws,
        locationIds: locationIds.length > 0 ? locationIds : undefined,
        occupationIds: occupationIds.length > 0 ? occupationIds : undefined,
        seniorityIds: seniorityIds.length > 0 ? seniorityIds : undefined,
        technologyIds: technologyIds.length > 0 ? technologyIds : undefined,
        employmentTypes: etypes.length > 0 ? etypes : undefined,
        salaryMinEur: salMinEur,
        salaryMaxEur: salMaxEur,
        experienceMin: expMin,
        experienceMax: expMax,
        languages,
        locale: uiLocale,
        offset: 0,
        limit: PAGE_SIZE,
      });
      setPostings(result.postings);
      setActiveCount(result.activeCount);
      setYearCount(result.yearCount);
      setExhausted(result.postings.length < PAGE_SIZE);
    });
  }

  // Register pageActions so the header SearchBar can interact directly
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
        setShowPostingId(null);
        updateUrl(null);
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
      placeholder: searchPlaceholder,
    });
    return () => setPageActions(null);
  }, [setPageActions, searchPlaceholder]);

  // Back-to-search link that carries current filters
  const searchHref = useMemo(
    () => buildFilteredPath(`/${uiLocale}/explore`, keywords, locations, undefined, occupations, seniorities, technologies),
    [uiLocale, keywords, locations, occupations, seniorities, technologies],
  );

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

  const handleSubmitSearch = useCallback(
    (nextKeywords: string[], nextLocations: SelectedLocation[], nextOccs?: TaxonomyItem[], nextSens?: TaxonomyItem[], nextTechs?: TaxonomyItem[]) => {
      setKeywords(nextKeywords); keywordsRef.current = nextKeywords;
      setLocations(nextLocations); locationsRef.current = nextLocations;
      if (nextOccs) { setOccupations(nextOccs); occupationsRef.current = nextOccs; }
      if (nextSens) { setSeniorities(nextSens); senioritiesRef.current = nextSens; }
      if (nextTechs) { setTechnologies(nextTechs); technologiesRef.current = nextTechs; }
      setShowPostingId(null);
      updateUrl(null);
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
    setSalaryCurrency(displayCurrency); salaryCurrencyRef.current = displayCurrency;
    setSalaryMin(undefined); salaryMinRef.current = undefined;
    setSalaryMax(undefined); salaryMaxRef.current = undefined;
    setExperienceMin(undefined); experienceMinRef.current = undefined;
    setExperienceMax(undefined); experienceMaxRef.current = undefined;
    setShowPostingId(null);
    updateUrl(null);
    runSearch();
  }, [displayCurrency]);

  async function handleLoadMore() {
    const locationIds = locations.map((l) => l.id);
    const occupationIds = occupations.length > 0 ? occupations.map((o) => o.id) : undefined;
    const seniorityIds = seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined;
    const technologyIds = technologies.length > 0 ? technologies.map((t) => t.id) : undefined;
    const etypes = employmentTypes.length > 0 ? employmentTypes : undefined;
    const salMinEur = toEur(salaryMin);
    const salMaxEur = toEur(salaryMax);

    const result = await getCompanyPostings({
      companyId: company.id,
      keywords,
      locationIds: locationIds.length > 0 ? locationIds : undefined,
      occupationIds,
      seniorityIds,
      technologyIds,
      employmentTypes: etypes,
      salaryMinEur: salMinEur,
      salaryMaxEur: salMaxEur,
      experienceMin,
      experienceMax,
      languages,
      locale: uiLocale,
      offset: postings.length,
      limit: PAGE_SIZE,
    });
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
    updateUrl(postingId);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    updateUrl(null);
  }

  const histogramFilters: HistogramFilters = useMemo(() => ({
    companyId: company.id,
    keywords: keywords.length > 0 ? keywords : undefined,
    locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
    occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
    seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
    technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
    languages: languages.length > 0 ? languages : undefined,
  }), [company.id, keywords, locations, occupations, seniorities, technologies, languages]);

  const metaParts: string[] = [];
  if (company.industryName) metaParts.push(company.industryName);
  if (company.employeeCountRange && EMPLOYEE_RANGE_LABELS[company.employeeCountRange]) {
    metaParts.push(t({
      id: "company.page.employees",
      comment: "Employee count range on company page",
      message: `${EMPLOYEE_RANGE_LABELS[company.employeeCountRange]} employees`,
    }));
  }
  if (company.foundedYear) {
    metaParts.push(t({
      id: "company.page.founded",
      comment: "Founded year on company page",
      message: `Founded ${company.foundedYear}`,
    }));
  }

  const mainContent = (
    <div className="space-y-4">
      {/* Back to search */}
      <BackLink href={searchHref}>
        <Trans id="company.page.backToSearch" comment="Back to search results link on company page">
          Search results
        </Trans>
      </BackLink>

      {/* Header */}
      <div className="flex items-center gap-3">
        {company.icon ? (
          <Image
            src={company.icon}
            alt={company.name}
            width={32}
            height={32}
            className="size-8 shrink-0 rounded"
          />
        ) : (
          <div className="flex size-8 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
            <Building2 size={18} />
          </div>
        )}
        {company.website ? (
          <a
            href={company.website}
            target="_blank"
            rel="noopener noreferrer"
            className="text-lg font-semibold hover:underline"
          >
            {company.name}
          </a>
        ) : (
          <span className="text-lg font-semibold">{company.name}</span>
        )}
        <StarButton companyId={company.id} />
      </div>

      {/* Tagline / description */}
      {company.description && (
        <p className="text-sm text-muted">{company.description}</p>
      )}

      {/* Meta */}
      {metaParts.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
          {metaParts.map((part, i) => (
            <span key={i}>{part}</span>
          ))}
        </div>
      )}

      {/* Stats */}
      <p className="text-xs text-muted">
        {activeCount} <Trans id="company.page.active" comment="Active postings count on company page">active</Trans>
        {" · "}
        {yearCount} <Trans id="company.page.yearCount" comment="Year postings count on company page">in the last year</Trans>
      </p>

      {/* Divider */}
      <hr className="border-divider" />

      {/* Search toolbar — same as main search page */}
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
          employmentTypesRef.current = updated;
          updateUrl();
          runSearch();
        }}
        onSalaryChange={handleSalaryChange}
        onExperienceChange={handleExperienceChange}
        histogramFilters={histogramFilters}
        onClearAll={handleClearAll}
        onSubmitSearch={handleSubmitSearch}
        searchPlaceholder={searchPlaceholder}
      />

      {/* Posting list */}
      {isSearching ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 size={20} className="animate-spin text-muted" />
        </div>
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
        </div>
      )}
    </div>
  );

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{mainContent}</div>
      {showPostingId && (
        <>
          <div className="hidden w-[420px] shrink-0 lg:block" aria-hidden="true" />
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
