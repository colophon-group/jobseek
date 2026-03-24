"use client";

import { useState, useRef, useMemo } from "react";
import Image from "next/image";
import Link from "next/link";
import { Building2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { useParams } from "next/navigation";
import { timeAgoShort } from "@/lib/time";
import { loadMorePostings } from "@/lib/actions/search";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { SaveButton } from "@/components/search/save-button";
import { StarButton } from "@/components/search/star-button";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SerializableLocation, SerializableOccupation, SerializableSeniority, SerializableTechnology } from "@/lib/search/query-params";
import type { SearchResultCompany, SearchResultPosting } from "@/lib/search";

const POSTINGS_BATCH = 20;

interface CompanyCardProps {
  result: SearchResultCompany;
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
  onShowPosting?: (postingId: string) => void;
  selectedPostingId?: string | null;
}

export function CompanyCard({ result, keywords, locationIds, locations, occupations, seniorities, technologies, employmentTypes, salaryMinEur, salaryMaxEur, experienceMin, experienceMax, languages, onShowPosting, selectedPostingId }: CompanyCardProps) {
  const params = useParams();
  const locale = (params.lang as string) ?? "en";
  const { company, activeMatches, yearMatches } = result;

  const companyHref = buildFilteredPath(
    `/${locale}/company/${company.slug}`,
    keywords,
    locations ?? [],
    undefined,
    occupations,
    seniorities,
    technologies,
  );

  const [extraPostings, setExtraPostings] = useState<SearchResultPosting[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const allPostings = useMemo(() => {
    const seen = new Set<string>();
    return [...result.postings, ...extraPostings].filter((p) => {
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });
  }, [result.postings, extraPostings]);

  const hasMore = !exhausted && allPostings.length < yearMatches;
  const offsetRef = useRef(result.postings.length);

  async function handleLoadMore() {
    const more = await loadMorePostings({
      companyId: company.id,
      keywords,
      locationIds,
      occupationIds: occupations?.map((o) => o.id),
      seniorityIds: seniorities?.map((s) => s.id),
      technologyIds: technologies?.map((t) => t.id),
      employmentTypes,
      salaryMinEur,
      salaryMaxEur,
      experienceMin,
      experienceMax,
      languages: languages ?? [locale],
      locale,
      offset: offsetRef.current,
      limit: POSTINGS_BATCH,
    });
    offsetRef.current += more.length;
    if (more.length > 0) {
      setExtraPostings((prev) => [...prev, ...more]);
    }
    if (more.length < POSTINGS_BATCH) {
      setExhausted(true);
    }
  }

  const { sentinelRef, isLoading } = useInfiniteScroll({ hasMore, load: handleLoadMore, root: scrollRef, rootMargin: "50px" });

  return (
    <div className="rounded-md border border-divider bg-surface p-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href={companyHref} prefetch={false} className="flex items-center gap-3 transition-opacity hover:opacity-80">
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
          <span className="text-sm font-semibold">{company.name}</span>
        </Link>
        <StarButton companyId={company.id} />
      </div>

      {/* Stats */}
      <p className="mt-2 text-xs text-muted">
        {activeMatches} <Trans id="search.card.active" comment="Active matches label on company card">active</Trans>
        {" · "}
        {yearMatches} <Trans id="search.card.yearCount" comment="Yearly matches label on company card">in the last year</Trans>
      </p>

      {/* Divider */}
      <hr className="my-3 border-divider" />

      {/* Scrollable posting list */}
      <div ref={scrollRef} className="max-h-[196px] overflow-y-auto scrollbar-hide">
        {allPostings.map((posting) => (
          <div
            key={posting.id}
            role="button"
            tabIndex={0}
            onClick={() => onShowPosting?.(posting.id)}
            onKeyDown={(e) => { if (e.key === "Enter") onShowPosting?.(posting.id); }}
            className={`flex cursor-pointer items-center gap-2 rounded px-1 py-1.5 transition-colors ${posting.id === selectedPostingId ? "bg-primary/10" : "hover:bg-border-soft"} ${posting.isActive === false ? "opacity-50" : ""}`}
          >
            <TrackingDot postingId={posting.id} />
            <span className="min-w-0 flex-1 truncate text-sm">{posting.title ?? "—"}</span>
            {posting.isActive === false && (
              <span className="shrink-0 rounded bg-border-soft px-1 py-0.5 text-[10px] text-muted">
                <Trans id="search.card.closed" comment="Label for inactive/closed job postings on company card">
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
        {hasMore && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} size="sm" />}
      </div>
    </div>
  );
}
