"use client";

import { useState, useCallback, useTransition, useRef, useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { X, MapPin } from "lucide-react";
import { SearchBar } from "@/components/search/search-bar";
import type { SelectedLocation } from "@/components/search/location-pills";
import { SearchResults } from "@/components/search/search-results";
import { ZeroResults } from "@/components/search/zero-results";
import { SkeletonCards } from "@/components/search/skeleton-card";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SearchResultCompany } from "@/lib/search";
import {
  useSearchStateStore,
  buildCacheKey,
} from "@/components/SearchStateProvider";

const PAGE_SIZE = 10;

interface SearchPageProps {
  initialCompanies: SearchResultCompany[];
  initialTotalCompanies: number;
  initialKeywords: string[];
  initialLocations: SelectedLocation[];
  language: string;
  userLat?: number;
  userLng?: number;
}

export function SearchPage({
  initialCompanies,
  initialTotalCompanies,
  initialKeywords,
  initialLocations,
  language,
  userLat,
  userLng,
}: SearchPageProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { get: getSearchState, set: setSearchState, setPageActions } = useSearchStateStore();

  // Restore from context if we have a cached snapshot.
  // Restore when: (a) URL has no filters (navigated back without explicit intent),
  // or (b) URL filters match the cached snapshot exactly.
  const cached = getSearchState();
  const currentCacheKey = buildCacheKey(
    initialKeywords,
    initialLocations.map((l) => l.id),
  );
  const hasUrlFilters = initialKeywords.length > 0 || initialLocations.length > 0;
  const shouldRestore =
    cached !== null &&
    (cached.cacheKey === currentCacheKey || !hasUrlFilters);

  const [keywords, setKeywords] = useState<string[]>(
    shouldRestore ? cached.keywords : initialKeywords,
  );
  const [locations, setLocations] = useState<SelectedLocation[]>(
    shouldRestore ? cached.locations : initialLocations,
  );
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
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const loadingRef = useRef(false);

  // Refs to hold current values for the unmount cleanup
  const keywordsRef = useRef(keywords);
  const locationsRef = useRef(locations);
  const companiesRef = useRef(companies);
  const totalCompaniesRef = useRef(totalCompanies);
  const showPostingIdRef = useRef(showPostingId);
  keywordsRef.current = keywords;
  locationsRef.current = locations;
  companiesRef.current = companies;
  totalCompaniesRef.current = totalCompanies;
  showPostingIdRef.current = showPostingId;

  // Save state to context on unmount
  useEffect(() => {
    return () => {
      setSearchState({
        keywords: keywordsRef.current,
        locations: locationsRef.current,
        companies: companiesRef.current,
        totalCompanies: totalCompaniesRef.current,
        showPostingId: showPostingIdRef.current,
        scrollY: window.scrollY,
        cacheKey: buildCacheKey(
          keywordsRef.current,
          locationsRef.current.map((l) => l.id),
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
        updateUrl(keywordsRef.current, updated);
        runSearch(keywordsRef.current, updated);
      },
      getLocations: () => locationsRef.current,
      getKeywords: () => keywordsRef.current,
    });
  }, [setPageActions]);

  // Restore scroll position and sync URL on mount when restoring from cache
  useEffect(() => {
    if (shouldRestore) {
      // Sync URL to reflect restored filters
      const url = buildFilteredPath(
        pathname,
        cached.keywords,
        cached.locations,
        cached.showPostingId ? { show: cached.showPostingId } : undefined,
      );
      window.history.replaceState(null, "", url);

      if (cached.scrollY > 0) {
        requestAnimationFrame(() => {
          window.scrollTo(0, cached.scrollY);
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasMore = companies.length < totalCompanies;
  const hasFilters = keywords.length > 0 || locations.length > 0;

  function updateUrl(kws: string[], locs: SelectedLocation[], showId?: string | null) {
    const url = buildFilteredPath(pathname, kws, locs, showId ? { show: showId } : undefined);
    window.history.replaceState(null, "", url);
  }

  function handleOpenPosting(postingId: string) {
    setShowPostingId(postingId);
    updateUrl(keywords, locations, postingId);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    updateUrl(keywords, locations, null);
  }

  function runSearch(kws: string[], locs: SelectedLocation[]) {
    const locationIds = locs.map((l) => l.id);
    startSearch(async () => {
      const result =
        kws.length > 0
          ? await searchJobs({
              keywords: kws,
              locationIds,
              language,
              offset: 0,
              limit: PAGE_SIZE,
            })
          : await listTopCompanies({
              locationIds,
              language,
              offset: 0,
              limit: PAGE_SIZE,
            });
      setCompanies(result.companies);
      setTotalCompanies(result.totalCompanies);
    });
  }

  const handleAddKeyword = useCallback(
    (keyword: string) => {
      const updated = [...keywords, keyword];
      setKeywords(updated);
      updateUrl(updated, locations);
      runSearch(updated, locations);
    },
    [keywords, locations, language, pathname],
  );

  const handleRemoveKeyword = useCallback(
    (keyword: string) => {
      const updated = keywords.filter((k) => k !== keyword);
      setKeywords(updated);
      updateUrl(updated, locations);
      runSearch(updated, locations);
    },
    [keywords, locations, language, pathname],
  );

  const handleAddLocation = useCallback(
    (location: SelectedLocation) => {
      const updated = [...locations, location];
      setLocations(updated);
      updateUrl(keywords, updated);
      runSearch(keywords, updated);
    },
    [keywords, locations, language, pathname],
  );

  const handleRemoveLocation = useCallback(
    (locationId: number) => {
      const updated = locations.filter((l) => l.id !== locationId);
      setLocations(updated);
      updateUrl(keywords, updated);
      runSearch(keywords, updated);
    },
    [keywords, locations, language, pathname],
  );

  const handleLoadMore = useCallback(() => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setIsLoadingMore(true);

    const offset = companies.length;
    const locationIds = locations.map((l) => l.id);
    const fetcher =
      keywords.length > 0
        ? searchJobs({ keywords, locationIds, language, offset, limit: PAGE_SIZE })
        : listTopCompanies({ locationIds, language, offset, limit: PAGE_SIZE });

    fetcher
      .then((result) => {
        setCompanies((prev) => {
          const seen = new Set(prev.map((c) => c.company.id));
          return [
            ...prev,
            ...result.companies.filter((c) => !seen.has(c.company.id)),
          ];
        });
        setTotalCompanies(result.totalCompanies);
      })
      .finally(() => {
        loadingRef.current = false;
        setIsLoadingMore(false);
      });
  }, [companies.length, keywords, locations, language]);

  const searchColumn = (
    <div className="space-y-6">
      <div className="space-y-3">
        {/* Mobile-only: search bar is in the header on desktop */}
        <div className="md:hidden">
          <SearchBar
            onAddLocation={handleAddLocation}
            locale={language}
            keywords={keywords}
            locations={locations}
            userLat={userLat}
            userLng={userLng}
          />
        </div>
        {(keywords.length > 0 || locations.length > 0) && (
          <div className="flex flex-wrap items-center gap-2">
            {keywords.map((kw) => (
              <span
                key={kw}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                {kw}
                <button
                  onClick={() => handleRemoveKeyword(kw)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            {locations.map((loc) => (
              <span
                key={loc.id}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-sm text-primary"
              >
                <MapPin size={12} className="shrink-0" />
                {loc.parentName && loc.type !== "country" && loc.type !== "macro"
                  ? `${loc.name}, ${loc.parentName}`
                  : loc.name}
                <button
                  onClick={() => handleRemoveLocation(loc.id)}
                  className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20 cursor-pointer"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

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
          hasMore={hasMore}
          onLoadMore={handleLoadMore}
          isLoadingMore={isLoadingMore}
          onShowPosting={handleOpenPosting}
        />
      )}
    </div>
  );

  if (!showPostingId) {
    return searchColumn;
  }

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{searchColumn}</div>
      <div className="hidden w-[420px] shrink-0 lg:block">
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
    </div>
  );
}
