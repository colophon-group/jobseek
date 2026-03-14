"use client";

import { useEffect, useRef } from "react";
import { CompanyCard } from "./company-card";
import { RequestCompanyPrompt } from "./request-company";
import type { SearchResultCompany } from "@/lib/search";
import type { SerializableLocation } from "@/lib/search/query-params";

interface SearchResultsProps {
  companies: SearchResultCompany[];
  keywords: string[];
  locationIds?: number[];
  locations?: SerializableLocation[];
  hasMore: boolean;
  onLoadMore: () => void;
  isLoadingMore: boolean;
  onShowPosting?: (postingId: string) => void;
}

export function SearchResults({
  companies,
  keywords,
  locationIds,
  locations,
  hasMore,
  onLoadMore,
  isLoadingMore,
  onShowPosting,
}: SearchResultsProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Infinite scroll sentinel
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || !hasMore) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          onLoadMore();
        }
      },
      { rootMargin: "200px" },
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, onLoadMore]);

  return (
    <div className="space-y-3">
      {companies.map((result) => (
        <div key={`${result.company.id}-${keywords.join(",")}`}>
          <CompanyCard result={result} keywords={keywords} locationIds={locationIds} locations={locations} onShowPosting={onShowPosting} />
        </div>
      ))}
      {hasMore && <div ref={sentinelRef} className="h-1" />}
      {isLoadingMore && <SkeletonCards count={2} />}
      {!hasMore && <RequestCompanyPrompt />}
    </div>
  );
}

function SkeletonCards({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="animate-pulse rounded-md border border-divider bg-surface p-4"
        >
          <div className="flex items-center gap-3">
            <div className="size-8 rounded bg-border-soft" />
            <div className="h-4 w-32 rounded bg-border-soft" />
          </div>
          <div className="mt-3 h-3 w-40 rounded bg-border-soft" />
          <hr className="my-3 border-divider" />
          <div className="space-y-2">
            <div className="h-4 w-full rounded bg-border-soft" />
            <div className="h-4 w-3/4 rounded bg-border-soft" />
            <div className="h-4 w-5/6 rounded bg-border-soft" />
          </div>
        </div>
      ))}
    </>
  );
}
