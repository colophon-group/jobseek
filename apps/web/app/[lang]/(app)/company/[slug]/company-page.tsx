"use client";

import { useState, useCallback, useTransition, useRef, useEffect, useMemo } from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowLeft, Building2, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { FollowButton } from "@/components/search/follow-button";
import { useParams, usePathname, useSearchParams } from "next/navigation";
import { timeAgoShort } from "@/lib/time";
import { SaveButton } from "@/components/search/save-button";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { FilterBar } from "@/components/search/filter-bar";
import type { FilterItem } from "@/components/search/filter-bar";
import { getCompanyPostings } from "@/lib/actions/company";
import type { CompanyDetail, CompanyLocation } from "@/lib/actions/company";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SearchResultPosting } from "@/lib/search";

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

interface CompanyPageProps {
  company: CompanyDetail;
  initialPostings: SearchResultPosting[];
  initialActiveCount: number;
  initialYearCount: number;
  initialFilters: FilterItem[];
  initialShowPostingId: string | null;
  topLocations: CompanyLocation[];
  totalLocationCount: number;
  language: string;
  userLat?: number;
  userLng?: number;
}

export function CompanyPage({
  company,
  initialPostings,
  initialActiveCount,
  initialYearCount,
  initialFilters,
  initialShowPostingId,
  topLocations,
  totalLocationCount,
  language,
  userLat,
  userLng,
}: CompanyPageProps) {
  const { t } = useLingui();
  const params = useParams();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const locale = (params.lang as string) ?? language;

  const [filters, setFilters] = useState<FilterItem[]>(initialFilters);
  const [postings, setPostings] = useState<SearchResultPosting[]>(initialPostings);
  const [activeCount, setActiveCount] = useState(initialActiveCount);
  const [yearCount, setYearCount] = useState(initialYearCount);
  const [showPostingId, setShowPostingId] = useState<string | null>(
    initialShowPostingId ?? searchParams.get("show"),
  );
  const [isSearching, startSearch] = useTransition();
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(initialPostings.length < PAGE_SIZE);
  const loadingRef = useRef(false);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const keywords = filters.filter((f) => f.kind === "keyword").map((f) => f.value);
  const locationFilters = filters.filter((f): f is FilterItem & { kind: "location" } => f.kind === "location");
  const locationIds = locationFilters.map((f) => f.id);
  const hasMore = !exhausted && postings.length < yearCount;

  function updateUrl(currentFilters: FilterItem[], showId?: string | null) {
    const kws = currentFilters.filter((f) => f.kind === "keyword").map((f) => f.value);
    const locs = currentFilters.filter((f): f is FilterItem & { kind: "location" } => f.kind === "location");
    const url = buildFilteredPath(pathname, kws, locs, showId ? { show: showId } : undefined);
    window.history.replaceState(null, "", url);
  }

  // Back-to-search link that carries current filters
  const searchHref = useMemo(
    () => buildFilteredPath(`/${locale}/app`, keywords, locationFilters),
    [locale, keywords, locationFilters],
  );

  // Fetch postings when filters change
  const handleFiltersChange = useCallback(
    (newFilters: FilterItem[]) => {
      setFilters(newFilters);
      updateUrl(newFilters, showPostingId);
      const kws = newFilters.filter((f) => f.kind === "keyword").map((f) => f.value);
      const locIds = newFilters.filter((f) => f.kind === "location").map((f) => f.id);
      startSearch(async () => {
        const result = await getCompanyPostings({
          companyId: company.id,
          keywords: kws,
          locationIds: locIds.length > 0 ? locIds : undefined,
          language: locale,
          offset: 0,
          limit: PAGE_SIZE,
        });
        setPostings(result.postings);
        setActiveCount(result.activeCount);
        setYearCount(result.yearCount);
        setExhausted(result.postings.length < PAGE_SIZE);
      });
    },
    [company.id, locale, showPostingId, pathname],
  );

  const handleLoadMore = useCallback(() => {
    if (loadingRef.current || !hasMore) return;
    loadingRef.current = true;
    setIsLoadingMore(true);

    getCompanyPostings({
      companyId: company.id,
      keywords,
      locationIds: locationIds.length > 0 ? locationIds : undefined,
      language: locale,
      offset: postings.length,
      limit: PAGE_SIZE,
    })
      .then((result) => {
        if (result.postings.length > 0) {
          setPostings((prev) => {
            const seen = new Set(prev.map((p) => p.id));
            return [...prev, ...result.postings.filter((p) => !seen.has(p.id))];
          });
        }
        if (result.postings.length < PAGE_SIZE) {
          setExhausted(true);
        }
      })
      .finally(() => {
        loadingRef.current = false;
        setIsLoadingMore(false);
      });
  }, [company.id, keywords, locationIds, locale, postings.length, hasMore]);

  // IntersectionObserver for infinite scroll
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) handleLoadMore();
      },
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [handleLoadMore]);

  function handleOpenPosting(postingId: string) {
    setShowPostingId(postingId);
    updateUrl(filters, postingId);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    updateUrl(filters, null);
  }

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
      <Link
        href={searchHref}
        className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-foreground"
      >
        <ArrowLeft size={12} />
        <Trans id="company.page.backToSearch" comment="Back to search results link on company page">
          Search results
        </Trans>
      </Link>

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
        <FollowButton companyId={company.id} />
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

      {/* Filter bar */}
      <FilterBar
        suggestedLocations={topLocations}
        totalLocationCount={totalLocationCount}
        companyId={company.id}
        filters={filters}
        onFiltersChange={handleFiltersChange}
        locale={locale}
        userLat={userLat}
        userLng={userLng}
      />

      {/* Posting list */}
      {isSearching ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 size={20} className="animate-spin text-muted" />
        </div>
      ) : postings.length === 0 ? (
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
            <div ref={sentinelRef} className="flex h-8 items-center justify-center">
              {isLoadingMore && <Loader2 size={14} className="animate-spin text-muted" />}
            </div>
          )}
        </div>
      )}
    </div>
  );

  if (!showPostingId) {
    return mainContent;
  }

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{mainContent}</div>
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
