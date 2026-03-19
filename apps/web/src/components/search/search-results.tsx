"use client";

import { CompanyCard } from "./company-card";
import { RequestCompanyPrompt } from "./request-company";
import type { SearchResultCompany } from "@/lib/search";
import type { SerializableLocation, SerializableOccupation, SerializableSeniority, SerializableTechnology } from "@/lib/search/query-params";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";

interface SearchResultsProps {
  companies: SearchResultCompany[];
  keywords: string[];
  locationIds?: number[];
  locations?: SerializableLocation[];
  occupations?: SerializableOccupation[];
  seniorities?: SerializableSeniority[];
  technologies?: SerializableTechnology[];
  languages?: string[];
  hasMore: boolean;
  onLoadMore: () => void;
  isLoadingMore: boolean;
  onShowPosting?: (postingId: string) => void;
  selectedPostingId?: string | null;
}

export function SearchResults({
  companies,
  keywords,
  locationIds,
  locations,
  occupations,
  seniorities,
  technologies,
  languages,
  hasMore,
  onLoadMore,
  isLoadingMore,
  onShowPosting,
  selectedPostingId,
}: SearchResultsProps) {
  const sentinelRef = useInfiniteScroll({ hasMore, isLoading: isLoadingMore, onLoadMore });

  return (
    <div className="space-y-3">
      {companies.map((result) => (
        <div key={`${result.company.id}-${keywords.join(",")}`}>
          <CompanyCard result={result} keywords={keywords} locationIds={locationIds} locations={locations} occupations={occupations} seniorities={seniorities} technologies={technologies} languages={languages} onShowPosting={onShowPosting} selectedPostingId={selectedPostingId} />
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
