"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { Building2, Loader2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { useParams } from "next/navigation";
import { timeAgoShort } from "@/lib/time";
import { loadMorePostings } from "@/lib/actions/search";
import { SaveButton } from "@/components/search/save-button";
import { FollowButton } from "@/components/search/follow-button";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SerializableLocation } from "@/lib/search/query-params";
import type { SearchResultCompany, SearchResultPosting } from "@/lib/search";

const POSTINGS_BATCH = 20;

interface CompanyCardProps {
  result: SearchResultCompany;
  keywords: string[];
  locationIds?: number[];
  locations?: SerializableLocation[];
  onShowPosting?: (postingId: string) => void;
}

export function CompanyCard({ result, keywords, locationIds, locations, onShowPosting }: CompanyCardProps) {
  const params = useParams();
  const locale = (params.lang as string) ?? "en";
  const { company, activeMatches, yearMatches } = result;

  const companyHref = buildFilteredPath(
    `/${locale}/company/${company.slug}`,
    keywords,
    locations ?? [],
  );

  const [extraPostings, setExtraPostings] = useState<SearchResultPosting[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(false);

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
  const hasMoreRef = useRef(hasMore);
  hasMoreRef.current = hasMore;

  const handleLoadMore = useCallback(() => {
    if (loadingRef.current || !hasMoreRef.current) return;
    loadingRef.current = true;
    setIsLoading(true);

    loadMorePostings({
      companyId: company.id,
      keywords,
      locationIds,
      language: locale,
      offset: offsetRef.current,
      limit: POSTINGS_BATCH,
    })
      .then((more) => {
        offsetRef.current += more.length;
        if (more.length > 0) {
          setExtraPostings((prev) => [...prev, ...more]);
        }
        if (more.length < POSTINGS_BATCH) {
          setExhausted(true);
        }
      })
      .finally(() => {
        setIsLoading(false);
        loadingRef.current = false;
      });
  }, [company.id, keywords, locationIds, locale]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      if (!hasMoreRef.current || loadingRef.current) return;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
      if (nearBottom) handleLoadMore();
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [handleLoadMore]);

  return (
    <div className="rounded-md border border-divider bg-surface p-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href={companyHref} className="flex items-center gap-3 transition-opacity hover:opacity-80">
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
        <FollowButton companyId={company.id} />
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
      <div ref={scrollRef} className="max-h-[196px] overflow-y-auto">
        {allPostings.map((posting) => (
          <div
            key={posting.id}
            role="button"
            tabIndex={0}
            onClick={() => onShowPosting?.(posting.id)}
            onKeyDown={(e) => { if (e.key === "Enter") onShowPosting?.(posting.id); }}
            className="flex cursor-pointer items-center gap-2 rounded px-1 py-1.5 transition-colors hover:bg-border-soft"
          >
            <span className="min-w-0 flex-1 truncate text-sm">{posting.title ?? "—"}</span>
            {posting.locations.length > 0 && (
              <span className={`shrink-0 text-xs text-muted ${posting.locations[0].geoType && posting.locations[0].geoType !== "city" ? "italic" : ""}`}>
                {posting.locations[0].name}
                {posting.locations.length > 1 && ` +${posting.locations.length - 1}`}
              </span>
            )}
            <SaveButton postingId={posting.id} />
            <span suppressHydrationWarning className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted">
              {timeAgoShort(posting.firstSeenAt)}
            </span>
          </div>
        ))}
        {hasMore && (
          <div className="flex h-6 items-center justify-center">
            {isLoading && <Loader2 size={12} className="animate-spin text-muted" />}
          </div>
        )}
      </div>
    </div>
  );
}
