"use client";

import { type ReactNode, useEffect, useState, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { Bookmark, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { CompanyIcon } from "@/components/CompanyIcon";
import { timeAgoShort } from "@/lib/time";
import { type WatchlistPostingEntry } from "@/lib/actions/watchlists";
import {
  runGetWatchlistPostings,
  runGetWatchlistPostingYearCount,
} from "@/lib/search/search-runner";
import { useSession } from "@/components/providers/SessionProvider";
import { useSavedJobs } from "@/components/providers/SavedJobsProvider";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { MobileJobDetailDialog } from "@/components/search/mobile-job-detail-dialog";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { usePaginatedLoadMore } from "@/lib/use-paginated-load-more";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { LanguageStatsRow } from "@/components/search/language-stats-row";
import { SearchUnavailable } from "@/components/search/search-unavailable";
import { formatDateDivider, getDateKey } from "@/components/watchlist/format-date-divider";
import { logExternalError } from "@/lib/safe-external-error";
import type {
  AiFilterAcceptedPage,
  AiFilterUiState,
} from "@/lib/ai-filter/ui-contract";
import { useAiFilteredResults } from "./use-ai-filtered-results";
import { ANON_MAX_WATCHLIST_POSTINGS } from "@/lib/search/constants";

const BATCH = 20;
function formatLocationSummary(locationNames: string[] | undefined): string {
  const names = [...new Set((locationNames ?? []).filter(Boolean))];
  if (names.length === 0) return "";
  return names.length === 1 ? names[0]! : `${names[0]} +${names.length - 1}`;
}

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
  initialTruncated = false,
  yearTotal,
  initialSearchUnavailable = false,
  jobLanguages,
  locale,
  onResultStateChange,
  onAiMatchCountChange,
  aiFilterState = null,
  initialAiAcceptedPage = null,
  onAiFilterStateChange,
  aiFilterScopeKey = "",
  aiFilterScopeReady = true,
  resultMode = "auto",
  drawerControl,
  drawerOpen = false,
  candidateTotal,
  aiFilterReadOnly = false,
  sharedSnapshot = false,
}: {
  filters: WatchlistJobListFilters;
  initialPostings: WatchlistPostingEntry[];
  initialTotal: number;
  initialTruncated?: boolean;
  yearTotal: number;
  initialSearchUnavailable?: boolean;
  jobLanguages: string[];
  locale: string;
  onResultStateChange?: (state: {
    candidateCount: number | undefined;
    unavailable: boolean;
  }) => void;
  onAiMatchCountChange?: (count: number) => void;
  aiFilterState?: AiFilterUiState | null;
  initialAiAcceptedPage?: AiFilterAcceptedPage | null;
  onAiFilterStateChange?: (state: AiFilterUiState) => void;
  aiFilterScopeKey?: string;
  aiFilterScopeReady?: boolean;
  /** Broad remains the default feed; narrowed is rendered in the expandable results panel. */
  resultMode?: "auto" | "broad" | "narrowed";
  /** Compact control and narrowed surface inserted between stats and results. */
  drawerControl?: ReactNode;
  /** Removes the replaced broad list from layout while preserving its state. */
  drawerOpen?: boolean;
  /** Current broad-feed size, used to report completed coverage precisely. */
  candidateTotal?: number;
  /** Shared viewers page persisted matches without starting new evaluations. */
  aiFilterReadOnly?: boolean;
  /** Public snapshots keep the owner's language scope and immutable controls. */
  sharedSnapshot?: boolean;
}) {
  const { i18n, t } = useLingui();
  const { isLoggedIn, isPending: isSessionPending } = useSession();
  const isLoggedInRef = useRef(isLoggedIn);
  isLoggedInRef.current = isLoggedIn;
  const searchParams = useSearchParams();
  const [showPostingId, setShowPostingId] = useState<string | null>(searchParams.get("show"));
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const { isSaved, toggle } = useSavedJobs();

  const todayLabel = t({ id: "watchlists.jobList.today", comment: "Date divider label for today", message: "Today" });
  const yesterdayLabel = t({ id: "watchlists.jobList.yesterday", comment: "Date divider label for yesterday", message: "Yesterday" });

  const filtersKey = JSON.stringify(filters);
  const [searchUnavailable, setSearchUnavailable] = useState(initialSearchUnavailable);

  // Pagination state machine. `filtersKey` doubles as the reset key —
  // changing filters re-fetches page 1 and clears local state.
  const normalResults = usePaginatedLoadMore<WatchlistPostingEntry>({
    initialItems: initialPostings,
    initialTotal,
    initialTruncated,
    batchSize: BATCH,
    itemKey: (p) => p.id,
    resetKey: resultMode === "narrowed" ? "narrowed-surface" : filtersKey,
    preserveDocumentScroll: resultMode !== "narrowed",
    fetcher: async ({ offset, limit }) => {
      try {
        const result = await runGetWatchlistPostings(
          { ...filtersRef.current, offset, limit },
          isLoggedInRef.current,
        );
        setSearchUnavailable(false);
        return result;
      } catch (error) {
        // A failed first page means the requested feed is unavailable. A
        // later-page failure must not replace an already-useful list with a
        // full-page error: keep the committed rows and let infinite scroll
        // retry after the sentinel leaves and re-enters the viewport.
        if (offset === 0) setSearchUnavailable(true);
        logExternalError(
          "warn",
          { service: "typesense", operation: "watchlist_load_more" },
          error,
        );
        throw error;
      }
    },
  });
  const aiResults = useAiFilteredResults({
    active: resultMode !== "broad",
    watchlistId: aiFilterState?.watchlistId ?? "",
    state: aiFilterState,
    initialPage: initialAiAcceptedPage,
    scopeKey: aiFilterScopeKey,
    scopeReady: aiFilterScopeReady,
    jobLanguages,
    locale,
    readOnly: aiFilterReadOnly,
    onStateChange: onAiFilterStateChange,
  });
  const aiFilterActive = resultMode !== "broad" && aiFilterState?.enabled === true;
  const postings = aiFilterActive ? aiResults.postings : normalResults.items;
  const total = aiFilterActive ? aiResults.total : normalResults.total;

  // Year-count refetch on filter change. The SSR-prerendered
  // `yearTotal` only reflects the watchlist's stored filters at page
  // load — when the owner edits filters in-place the active count
  // updates (via `usePaginatedLoadMore`'s reset-on-filtersKey) but the
  // year count went stale until a full reload. Mirror that reset by
  // keying a `useEffect` on the same `filtersKey` so both badges stay
  // in lockstep. Issue #3344.
  //
  // `initialFiltersKeyRef` skips the first fetch on mount — the SSR
  // `yearTotal` already covers it. Cancel-on-stale guards a race
  // between rapid filter changes (we only commit the latest
  // in-flight result).
  const [yearTotal_, setYearTotal] = useState(yearTotal);
  const initialFiltersKeyRef = useRef(filtersKey);
  useEffect(() => {
    if (resultMode === "narrowed") return;
    if (filtersKey === initialFiltersKeyRef.current) return;
    setSearchUnavailable(false);
    let cancelled = false;
    runGetWatchlistPostingYearCount(filtersRef.current).then((next) => {
      if (cancelled) return;
      setYearTotal(next);
    }).catch((err) => {
      // Keep the SSR count when this secondary statistic cannot refresh;
      // the jobs already on screen remain valid and usable.
      logExternalError("error", { service: "typesense", operation: "watchlist_year_count" }, err);
    });
    return () => {
      cancelled = true;
    };
  }, [filtersKey, resultMode]);

  const anonymousNarrowedLimitReached = aiFilterActive &&
    !isSessionPending &&
    !isLoggedIn &&
    postings.length >= ANON_MAX_WATCHLIST_POSTINGS;
  const infiniteHasMore = aiFilterActive
    ? aiFilterScopeReady &&
      aiResults.hasMore &&
      !aiResults.unavailable &&
      !isSessionPending &&
      !anonymousNarrowedLimitReached
    // A signed-in viewer briefly has `isLoggedIn=false` while the client
    // session hydrates. Loading offset 20 during that window applies the
    // anonymous 20-job cap and permanently marks the list truncated. Wait
    // for the authoritative session before the first broad-page request.
    : !isSessionPending && normalResults.hasMore;
  const { sentinelRef, isLoading: isInfiniteLoading } = useInfiniteScroll({
    hasMore: infiniteHasMore,
    load: aiFilterActive ? aiResults.loadMore : normalResults.loadMore,
    rootMargin: "400px",
    observerKey: aiFilterActive
      ? `${aiFilterState?.queryVersionId ?? "none"}:${aiFilterScopeKey}:${aiResults.postings.length}`
      : filtersKey,
  });
  const isLoading = aiFilterActive
    ? aiResults.isLoading || isInfiniteLoading
    : isInfiniteLoading;
  const showUnavailable = aiFilterActive
    ? aiResults.unavailable || (!aiFilterReadOnly && (
      aiFilterState.status === "provider_unavailable" ||
      aiFilterState.status === "paused_kill" || aiFilterState.status === "failed" ||
      aiFilterState.status === "paused_budget" ||
      aiFilterState.status === "paused_entitlement"
    ))
    : searchUnavailable || (
      postings.length === 0 && !isLoading && total > 0
  );

  useEffect(() => {
    if (resultMode === "narrowed") return;
    onResultStateChange?.({
      candidateCount: searchUnavailable ? undefined : normalResults.total,
      unavailable: searchUnavailable,
    });
  }, [
    onResultStateChange,
    normalResults.resultRevision,
    normalResults.total,
    resultMode,
    searchUnavailable,
  ]);

  useEffect(() => {
    if (resultMode !== "narrowed" || !aiFilterActive) return;
    onAiMatchCountChange?.(aiResults.total);
  }, [
    aiFilterActive,
    aiResults.total,
    onAiMatchCountChange,
    resultMode,
  ]);

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
    const locationSummary = formatLocationSummary(entry.locationNames);
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
        className={`relative flex min-h-10 w-full min-w-0 max-w-full items-center gap-3 overflow-hidden rounded-md px-2 py-2 text-left transition-colors [contain:layout] hover:bg-border-soft ${
          showPostingId === entry.id ? "bg-border-soft" : ""
        }`}
      >
        <button
          type="button"
          onClick={() => handleOpenPosting(entry.id)}
          aria-label={
            entry.title
              ? `${entry.company.name} — ${entry.title}${locationSummary ? ` — ${locationSummary}` : ""}`
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

        <span className="max-w-28 shrink-0 truncate text-xs text-muted sm:max-w-40">
          {entry.company.name}
        </span>

        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{entry.title ?? "—"}</span>
          {locationSummary && (
            <span className="block truncate text-[10px] text-muted">
              {locationSummary}
            </span>
          )}
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
          {timeAgoShort(entry.firstSeenAt, locale)}
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
  const acceptedCountLabel = i18n._({
    id: "watchlists.jobList.acceptedCount",
    comment: "Number of matching jobs shown in a precisely narrowed watchlist",
    message: "{count, plural, one {# match} other {# matches}}",
    values: { count: total },
  });
  const evaluatedCountLabel = i18n._({
    id: "watchlists.jobList.evaluatedCount",
    comment: "Number of watchlist candidates evaluated against the saved request",
    message: "{count, plural, one {# evaluated} other {# evaluated}}",
    values: {
      count: aiFilterState?.status === "caught_up" && candidateTotal != null
        ? candidateTotal
        : candidateTotal == null
          ? aiResults.evaluated
          : Math.min(aiResults.evaluated, candidateTotal),
    },
  });
  const listColumn = (
    <div className="min-w-0">
      <div
        id={resultMode === "narrowed" ? undefined : "watchlist-results-boundary"}
        className={resultMode === "narrowed"
          ? "sticky top-14 z-20 -mx-2 space-y-2 bg-surface-glass px-2 py-2 backdrop-blur-md md:top-[5.5rem]"
          : "sticky top-0 z-30 -mx-2 flex min-h-10 scroll-mt-0 items-center bg-background/80 px-2 py-2 backdrop-blur-md md:top-12 md:scroll-mt-12"}
      >
        {resultMode !== "narrowed" && (
          <LanguageStatsRow
            jobLanguages={jobLanguages}
            locale={locale}
            activeCount={normalResults.total}
            yearCount={yearTotal_}
            allowLanguageChange={!sharedSnapshot}
          />
        )}
        {resultMode !== "broad" && aiFilterActive && (
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-1 text-xs">
            <span className="font-medium text-foreground">
              <Trans
                id="watchlists.jobList.preciseResults"
                comment="Status label above a watchlist narrowed by natural-language matching"
              >
                Matching results
              </Trans>
            </span>
            <span className="shrink-0 rounded-full bg-primary/10 px-2.5 py-1 font-medium tabular-nums text-primary">
              {acceptedCountLabel} · {evaluatedCountLabel}
            </span>
          </div>
        )}
      </div>
      {resultMode === "broad" && drawerControl ? (
        <div className="mt-2 min-w-0">{drawerControl}</div>
      ) : null}
      {/* `[overflow-anchor:none]` opts the whole postings list out of the
          browser's automatic scroll-anchor selection. Without it, when
          pagination appends new rows the anchoring heuristic can pick a
          row near the viewport edge and adjust scroll position by a few
          pixels, which the user perceives as cards "jumping up and down"
          (closes #3345). The sentinel inside `InfiniteScrollSentinel`
          already opts out for itself; this widens the opt-out to every
          row + date divider so no element in the subtree can be picked
          mid-scroll. */}
      <div className={`relative grid w-full min-w-0 max-w-full ${
        resultMode === "narrowed" ? "mt-1" : "mt-4"
      }`}>
        <div className={`col-start-1 row-start-1 min-w-0 max-w-full [overflow-anchor:none] ${
          drawerOpen ? "invisible h-0 overflow-hidden" : ""
        }`}>
          {rows}

          {showUnavailable ? (
            <SearchUnavailable />
          ) : aiFilterActive && postings.length === 0 &&
              (isLoading || aiResults.hasMore) ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted" role="status">
              <Loader2 size={16} className="motion-safe:animate-spin" aria-hidden="true" />
              <Trans
                id="watchlists.jobList.evaluating"
                comment="Loading state while a saved matching request is evaluated against a watchlist"
              >
                Reviewing this feed…
              </Trans>
            </div>
          ) : postings.length === 0 && !isLoading && (
            <div className="py-12 text-center text-sm text-muted">
              {aiFilterActive ? (
                <Trans
                  id="watchlists.jobList.noPreciseMatches"
                  comment="Empty state when no jobs satisfy a watchlist's natural-language matching request"
                >
                  No jobs match these criteria.
                </Trans>
              ) : (
                <Trans id="watchlists.jobList.empty" comment="Empty state when no jobs match the time range">
                  No jobs found.
                </Trans>
              )}
            </div>
          )}

          {infiniteHasMore && !showUnavailable && (
            <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} />
          )}
          {!aiFilterActive && !normalResults.hasMore && normalResults.truncated && (
            <TruncationPrompt type="postings" />
          )}
          {anonymousNarrowedLimitReached && (
            <TruncationPrompt type="postings" />
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex w-full min-w-0 max-w-full gap-5">
      <div className="min-w-0 flex-1">{listColumn}</div>
      {showPostingId && (
        <>
          <div
            className="sticky top-[4.5rem] z-40 hidden h-[calc(100vh-5.5rem)] w-[420px] shrink-0 lg:block"
          >
            <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
          </div>
          <MobileJobDetailDialog postingId={showPostingId} onClose={handleClosePosting} />
        </>
      )}
    </div>
  );
}
