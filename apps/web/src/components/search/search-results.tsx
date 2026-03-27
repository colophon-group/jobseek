"use client";

import { CompanyCard } from "./company-card";
import { RequestCompanyPrompt } from "./request-company";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
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
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
  hasMore: boolean;
  truncated?: boolean;
  load: () => Promise<void>;
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
  employmentTypes,
  salaryMinEur,
  salaryMaxEur,
  experienceMin,
  experienceMax,
  languages,
  hasMore,
  truncated,
  load,
  onShowPosting,
  selectedPostingId,
}: SearchResultsProps) {
  const { sentinelRef, isLoading } = useInfiniteScroll({ hasMore, load });

  return (
    <div className="space-y-3">
      {companies.map((result) => (
        <div key={`${result.company.id}-${keywords.join(",")}`}>
          <CompanyCard result={result} keywords={keywords} locationIds={locationIds} locations={locations} occupations={occupations} seniorities={seniorities} technologies={technologies} employmentTypes={employmentTypes} salaryMinEur={salaryMinEur} salaryMaxEur={salaryMaxEur} experienceMin={experienceMin} experienceMax={experienceMax} languages={languages} onShowPosting={onShowPosting} selectedPostingId={selectedPostingId} />
        </div>
      ))}
      {hasMore && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} />}
      {!hasMore && truncated && <TruncationPrompt type="companies" />}
      {!hasMore && !truncated && <RequestCompanyPrompt />}
    </div>
  );
}
