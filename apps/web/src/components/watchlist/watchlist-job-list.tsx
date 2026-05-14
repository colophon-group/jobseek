"use client";

import { useState, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { Bookmark } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { CompanyIcon } from "@/components/CompanyIcon";
import { timeAgoShort } from "@/lib/time";
import { type WatchlistPostingEntry } from "@/lib/actions/watchlists";
import { runGetWatchlistPostings } from "@/lib/search/search-runner";
import { useClearTypesenseOnAuthChange } from "@/lib/search/use-clear-typesense-on-auth-change";
import { useSession } from "@/components/SessionProvider";
import { useSavedJobs } from "@/components/SavedJobsProvider";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { usePaginatedLoadMore } from "@/lib/use-paginated-load-more";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { LanguageStatsRow } from "@/components/search/language-stats-row";
import { formatDateDivider, getDateKey } from "@/components/watchlist/format-date-divider";

const BATCH = 20;

export interface WatchlistJobListFilters {
  companyIds: string[];
  anyCompany?: boolean;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  /** Work-mode filter — `onsite | hybrid | remote` (issue #3037). */
  workMode?: ("onsite" | "hybrid" | "remote")[];
  /** Employment-type filter (issue #3037). */
  employmentType?: string[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
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
  const { isLoggedIn } = useSession();
  const isLoggedInRef = useRef(isLoggedIn);
  isLoggedInRef.current = isLoggedIn;
  useClearTypesenseOnAuthChange(isLoggedIn);
  const searchParams = useSearchParams();
  const [showPostingId, setShowPostingId] = useState<string | null>(searchParams.get("show"));
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const { isSaved, toggle } = useSavedJobs();

  const todayLabel = t({ id: "watchlists.jobList.today", comment: "Date divider label for today", message: "Today" });
  const yesterdayLabel = t({ id: "watchlists.jobList.yesterday", comment: "Date divider label for yesterday", message: "Yesterday" });

  const filtersKey = JSON.stringify(filters);

  // Pagination state machine. `filtersKey` doubles as the reset key —
  // changing filters re-fetches page 1 and clears local state.
  const {
    items: postings,
    total,
    truncated: isTruncated,
    hasMore,
    loadMore,
  } = usePaginatedLoadMore<WatchlistPostingEntry>({
    initialItems: initialPostings,
    initialTotal,
    batchSize: BATCH,
    itemKey: (p) => p.id,
    resetKey: filtersKey,
    fetcher: ({ offset, limit }) =>
      runGetWatchlistPostings(
        { ...filtersRef.current, offset, limit },
        isLoggedInRef.current,
      ),
  });

  const { sentinelRef, isLoading } = useInfiniteScroll({ hasMore, load: loadMore });

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
      // `min-h-7` (= 28px = the divider's natural rendered height with
      // py-2 + ~12px text) locks vertical extent so the divider can never
      // cause a layout shift during scroll (closes #3345).
      rows.push(
        <div
          key={`d-${dateKey}`}
          className="flex min-h-7 items-center gap-3 px-2 py-2"
          suppressHydrationWarning
        >
          <div className="h-px flex-1 bg-divider" />
          <span className="text-[10px] font-medium uppercase tracking-wider text-muted" suppressHydrationWarning>
            {formatDateDivider(entry.firstSeenAt, todayLabel, yesterdayLabel, locale)}
          </span>
          <div className="h-px flex-1 bg-divider" />
        </div>,
      );
    }

    // Un-nested layout (issue #3166): row open button + save button are
    // siblings in a `relative` container, not nested. The open button is
    // a positioned overlay covering the row's click area; the save
    // button sits in normal flex flow with `relative z-10` so it stacks
    // above the overlay AND receives its own click. Tab order: row open
    // first, then save (DOM order). No `e.stopPropagation()` needed —
    // the buttons are siblings, not nested.
    rows.push(
      // `min-h-10` (= 40px = current natural row height) locks vertical
      // extent so any rounding / content-driven variation never causes a
      // layout shift during scroll or pagination (closes #3345).
      // `[contain:layout]` isolates per-row layout calculations so a
      // re-render of one row cannot reflow neighbouring rows.
      <div
        key={entry.id}
        className={`relative flex min-h-10 w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors [contain:layout] hover:bg-border-soft ${
          showPostingId === entry.id ? "bg-border-soft" : ""
        }`}
      >
        <button
          type="button"
          onClick={() => handleOpenPosting(entry.id)}
          aria-label={
            entry.title
              ? `${entry.company.name} — ${entry.title}`
              : t({
                  id: "watchlists.jobList.openPosting",
                  comment: "Aria label for the row open-posting button when the posting title is missing",
                  message: "Open job posting",
                })
          }
          className="absolute inset-0 z-0 cursor-pointer rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        />
        <TrackingDot postingId={entry.id} />
        <CompanyIcon icon={entry.company.icon} alt={entry.company.name} size={24} />

        <span className="shrink-0 text-xs text-muted">
          {entry.company.name}
        </span>

        <span className="min-w-0 flex-1 truncate text-sm">
          {entry.title ?? "—"}
        </span>

        {!entry.title && (
          // `relative z-10` so the warning icon's tooltip trigger remains
          // above the absolute overlay button and still receives hover.
          <span className="relative z-10 inline-flex shrink-0">
            <PendingJobIcon />
          </span>
        )}
        <button
          type="button"
          onClick={() => toggle(entry.id)}
          className="relative z-10 shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          aria-label={
            isSaved(entry.id)
              ? t({ id: "watchlists.jobList.unsave", comment: "Unsave job aria label", message: "Unsave job" })
              : t({ id: "watchlists.jobList.save", comment: "Save job aria label", message: "Save job" })
          }
        >
          <Bookmark
            size={14}
            aria-hidden="true"
            className={isSaved(entry.id) ? "fill-current" : ""}
          />
        </button>

        <span
          suppressHydrationWarning
          className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted"
        >
          {timeAgoShort(entry.firstSeenAt)}
        </span>
      </div>,
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
      {/* `[overflow-anchor:none]` opts the whole postings list out of the
          browser's automatic scroll-anchor selection. Without it, when
          pagination appends new rows the anchoring heuristic can pick a
          row near the viewport edge and adjust scroll position by a few
          pixels, which the user perceives as cards "jumping up and down"
          (closes #3345). The sentinel inside `InfiniteScrollSentinel`
          already opts out for itself; this widens the opt-out to every
          row + date divider so no element in the subtree can be picked
          mid-scroll. */}
      <div className="[overflow-anchor:none]">
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
