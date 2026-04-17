"use client";

import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Image from "next/image";
import { Building2, Bookmark } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { timeAgoShort } from "@/lib/time";
import {
  getWatchlistPostings,
  type WatchlistPostingEntry,
} from "@/lib/actions/watchlists";
import { useSavedJobs } from "@/components/SavedJobsProvider";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { LanguageStatsRow } from "@/components/search/language-stats-row";

const BATCH = 20;

export interface WatchlistJobListFilters {
  companyIds: string[];
  anyCompany?: boolean;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
}

function formatDateDivider(dateStr: string, todayLabel: string, yesterdayLabel: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());

  if (d.getTime() === today.getTime()) return todayLabel;
  if (d.getTime() === yesterday.getTime()) return yesterdayLabel;

  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function getDateKey(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function WatchlistJobList({
  filters,
  initialPostings,
  initialTotal,
  yearTotal,
  jobLanguages,
  locale,
}: {
  filters: WatchlistJobListFilters;
  initialPostings: WatchlistPostingEntry[];
  initialTotal: number;
  yearTotal: number;
  jobLanguages: string[];
  locale: string;
}) {
  const { t } = useLingui();
  const [postings, setPostings] = useState(initialPostings);
  const [total, setTotal] = useState(initialTotal);
  const [exhausted, setExhausted] = useState(initialPostings.length >= initialTotal);
  const [isTruncated, setIsTruncated] = useState(false);
  const searchParams = useSearchParams();
  const [showPostingId, setShowPostingId] = useState<string | null>(searchParams.get("show"));
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const { isSaved, toggle } = useSavedJobs();

  const todayLabel = t({ id: "watchlists.jobList.today", comment: "Date divider label for today", message: "Today" });
  const yesterdayLabel = t({ id: "watchlists.jobList.yesterday", comment: "Date divider label for yesterday", message: "Yesterday" });

  const filtersKey = JSON.stringify(filters);
  const initialFiltersKey = useRef(filtersKey);

  // Re-fetch only when filters actually change (not on initial mount —
  // the server already provided initialPostings/initialTotal)
  useEffect(() => {
    if (filtersKey === initialFiltersKey.current) return;
    getWatchlistPostings({ ...filters, offset: 0, limit: BATCH })
      .then(({ postings: p, total: t, truncated }) => {
        setPostings(p);
        setTotal(t);
        setExhausted(p.length >= t);
        setIsTruncated(truncated ?? false);
      });
  }, [filtersKey]);

  async function handleLoadMore() {
    const result = await getWatchlistPostings({
      ...filtersRef.current,
      offset: postings.length,
      limit: BATCH,
    });
    if (result.truncated) setIsTruncated(true);
    setTotal(result.total);
    if (result.postings.length > 0) {
      setPostings((prev) => {
        const seen = new Set(prev.map((p) => p.id));
        return [...prev, ...result.postings.filter((p) => !seen.has(p.id))];
      });
    }
    if (result.postings.length < BATCH) setExhausted(true);
  }

  const hasMore = !exhausted && !isTruncated;
  const { sentinelRef, isLoading } = useInfiniteScroll({ hasMore, load: handleLoadMore });

  function handleOpenPosting(postingId: string) {
    setShowPostingId(postingId);
    const url = new URL(window.location.href);
    url.searchParams.set("show", postingId);
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("show");
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  // Build entries with date dividers
  let lastDateKey = "";
  const rows: React.ReactNode[] = [];
  const seenDividers = new Set<string>();

  for (const entry of postings) {
    const dateKey = getDateKey(entry.firstSeenAt);
    if (dateKey !== lastDateKey && !seenDividers.has(dateKey)) {
      lastDateKey = dateKey;
      seenDividers.add(dateKey);
      rows.push(
        <div
          key={`d-${dateKey}`}
          className="flex items-center gap-3 px-2 py-2"
          suppressHydrationWarning
        >
          <div className="h-px flex-1 bg-divider" />
          <span className="text-[10px] font-medium uppercase tracking-wider text-muted" suppressHydrationWarning>
            {formatDateDivider(entry.firstSeenAt, todayLabel, yesterdayLabel)}
          </span>
          <div className="h-px flex-1 bg-divider" />
        </div>,
      );
    }

    rows.push(
      <button
        key={entry.id}
        type="button"
        onClick={() => handleOpenPosting(entry.id)}
        className={`flex w-full cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-border-soft ${
          showPostingId === entry.id ? "bg-border-soft" : ""
        }`}
      >
        <TrackingDot postingId={entry.id} />
        {entry.company.icon ? (
          <Image
            src={entry.company.icon}
            alt={entry.company.name}
            width={24}
            height={24}
            className="size-6 shrink-0 rounded"
          />
        ) : (
          <div className="flex size-6 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
            <Building2 size={14} />
          </div>
        )}

        <span className="shrink-0 text-xs text-muted">
          {entry.company.name}
        </span>

        <span className="min-w-0 flex-1 truncate text-sm">
          {entry.title ?? "—"}
        </span>

        {!entry.title && <PendingJobIcon />}
        <span
          role="button"
          onClick={(e) => {
            e.stopPropagation();
            toggle(entry.id);
          }}
          className="shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70"
          aria-label={
            isSaved(entry.id)
              ? t({ id: "watchlists.jobList.unsave", comment: "Unsave job aria label", message: "Unsave job" })
              : t({ id: "watchlists.jobList.save", comment: "Save job aria label", message: "Save job" })
          }
        >
          <Bookmark
            size={14}
            className={isSaved(entry.id) ? "fill-current" : ""}
          />
        </span>

        <span
          suppressHydrationWarning
          className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted"
        >
          {timeAgoShort(entry.firstSeenAt)}
        </span>
      </button>,
    );
  }

  // Stats row lives inside the left flex column so when the job
  // detail panel opens on the right, the "Showing jobs ... · N
  // active · M in the last year" line stays aligned to the postings
  // list (not spanning across both columns). `total` reflects the
  // live filter state, so the activeCount here updates if the user
  // edits the watchlist filters in-place.
  const listColumn = (
    <div className="space-y-4">
      <LanguageStatsRow
        jobLanguages={jobLanguages}
        locale={locale}
        activeCount={total}
        yearCount={yearTotal}
      />
      <div>
        {rows}

        {postings.length === 0 && !isLoading && (
          <div className="py-12 text-center text-sm text-muted">
            <Trans id="watchlists.jobList.empty" comment="Empty state when no jobs match the time range">
              No jobs found.
            </Trans>
          </div>
        )}

        {hasMore && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} />}
        {!hasMore && isTruncated && <TruncationPrompt type="postings" />}
      </div>
    </div>
  );

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{listColumn}</div>
      {showPostingId && (
        <>
          <div
            className="sticky top-[4.5rem] z-40 hidden h-[calc(100vh-5.5rem)] w-[420px] shrink-0 lg:block"
          >
            <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
          </div>
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
