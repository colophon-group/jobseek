"use client";

import { useState, useCallback, useTransition, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { KeywordPills } from "@/components/search/keyword-pills";
import { LocationPills } from "@/components/search/location-pills";
import type { SelectedLocation } from "@/components/search/location-pills";
import { SearchResults } from "@/components/search/search-results";
import { ZeroResults } from "@/components/search/zero-results";
import { SkeletonCards } from "@/components/search/skeleton-card";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import type { SearchResultCompany } from "@/lib/search";

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

  const [keywords, setKeywords] = useState<string[]>(initialKeywords);
  const [locations, setLocations] =
    useState<SelectedLocation[]>(initialLocations);
  const [showPostingId, setShowPostingId] = useState<string | null>(
    searchParams.get("show"),
  );
  const [companies, setCompanies] =
    useState<SearchResultCompany[]>(initialCompanies);
  const [totalCompanies, setTotalCompanies] = useState(initialTotalCompanies);
  const [isSearching, startSearch] = useTransition();
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const loadingRef = useRef(false);

  const hasMore = companies.length < totalCompanies;
  const hasFilters = keywords.length > 0 || locations.length > 0;

  function updateUrl(kws: string[], locs: SelectedLocation[], showId?: string | null) {
    const params = new URLSearchParams();
    if (kws.length > 0) params.set("q", kws.join(","));
    if (locs.length > 0)
      params.set(
        "loc",
        locs.map((l) => `${l.id}:${l.name}:${l.type}:${l.parentName ?? ""}`).join(";"),
      );
    if (showId) params.set("show", showId);
    const qs = params.toString();
    window.history.replaceState(null, "", pathname + (qs ? `?${qs}` : ""));
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
        <KeywordPills
          keywords={keywords}
          onAdd={handleAddKeyword}
          onRemove={handleRemoveKeyword}
        />
        <LocationPills
          locations={locations}
          onAdd={handleAddLocation}
          onRemove={handleRemoveLocation}
          locale={language}
          userLat={userLat}
          userLng={userLng}
        />
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
