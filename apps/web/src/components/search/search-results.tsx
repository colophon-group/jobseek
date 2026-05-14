"use client";

import { CompanyCard } from "./company-card";
import { RequestCompanyPrompt } from "./request-company";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
import type { SearchResultCompany, WorkMode } from "@/lib/search";
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
  workMode?: WorkMode[];
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
  workMode,
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
        // Keyword-keyed wrapper: forces each card to remount when the
        // user changes `keywords`, so the card's internal pagination
        // state (`extraPostings`, `exhausted`, `offsetRef`) resets and
        // doesn't show stale postings from a prior keyword search.
        // The post-#3198 `React.memo` on `CompanyCard` still skips
        // renders for non-keyword filter changes (salary, locations,
        // occupations, ...), which is the hot path called out in the
        // issue (salary slider drag).
        <div key={`${result.company.id}-${keywords.join(",")}`}>
          <CompanyCard result={result} keywords={keywords} locationIds={locationIds} locations={locations} occupations={occupations} seniorities={seniorities} technologies={technologies} employmentTypes={employmentTypes} workMode={workMode} salaryMinEur={salaryMinEur} salaryMaxEur={salaryMaxEur} experienceMin={experienceMin} experienceMax={experienceMax} languages={languages} onShowPosting={onShowPosting} selectedPostingId={selectedPostingId} />
        </div>
      ))}
      {hasMore && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} />}
      {!hasMore && truncated && <TruncationPrompt type="companies" />}
      {!hasMore && !truncated && <RequestCompanyPrompt />}
    </div>
  );
}
