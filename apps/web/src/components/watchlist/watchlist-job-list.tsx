"use client";

import { useCallback, useEffect, useState, useRef } from "react";
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
import { AI_FILTER_PREFETCH_CANDIDATES } from "@/lib/ai-filter/demand";

const BATCH = 20;
const AI_FILTER_POLL_MS = 750;
const AI_FILTER_POLL_ATTEMPTS = 60;

type AiDecisionPageResponse = {
  decisions: Array<{ posting: WatchlistPostingEntry }>;
  nextOffset: number;
  hasMore: boolean;
};

function isAiFilterTerminal(state: AiFilterUiState): boolean {
  return state.status === "caught_up" ||
    state.status === "paused_entitlement" ||
    state.status === "paused_budget" ||
    state.status === "provider_unavailable" ||
    state.status === "paused_kill" ||
    state.status === "cancelled" ||
    state.status === "failed" ||
    state.status === "disabled";
}

async function readAiFilterState(watchlistId: string): Promise<AiFilterUiState> {
  const response = await fetch(`/api/web/watchlists/${watchlistId}/ai-filter`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("matching_state_unavailable");
  return response.json() as Promise<AiFilterUiState>;
}

async function readAcceptedPage(
  watchlistId: string,
  offset: number,
  limit: number,
): Promise<AiDecisionPageResponse> {
  const params = new URLSearchParams({
    bucket: "accepted",
    offset: String(offset),
    limit: String(limit),
  });
  const response = await fetch(
    `/api/web/watchlists/${watchlistId}/ai-filter/decisions?${params}`,
    { credentials: "same-origin", cache: "no-store" },
  );
  if (!response.ok) throw new Error("matching_results_unavailable");
  return response.json() as Promise<AiDecisionPageResponse>;
}

async function requestAiFilterDemand(
  watchlistId: string,
  offset: number,
): Promise<void> {
  const response = await fetch(
    `/api/web/watchlists/${watchlistId}/ai-filter/reconcile`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offset }),
    },
  );
  if (!response.ok) throw new Error("matching_reconcile_unavailable");
}

function waitForAiFilterPoll(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, AI_FILTER_POLL_MS));
}

function useAiFilteredResults(input: {
  watchlistId: string;
  state: AiFilterUiState | null;
  initialPage: AiFilterAcceptedPage | null;
  scopeKey: string;
  scopeReady: boolean;
  onStateChange?: (state: AiFilterUiState) => void;
}) {
  const [postings, setPostings] = useState<WatchlistPostingEntry[]>(
    input.initialPage?.postings ?? [],
  );
  const [total, setTotal] = useState(input.state?.counts.accepted ?? 0);
  const [evaluated, setEvaluated] = useState(input.state?.counts.total ?? 0);
  const [hasMore, setHasMore] = useState(input.initialPage?.hasMore ?? false);
  const [isLoading, setIsLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const cursorRef = useRef(input.initialPage?.nextOffset ?? 0);
  const postingsRef = useRef(postings);
  const stateRef = useRef(input.state);
  const loadingRef = useRef(false);
  const generationRef = useRef(0);
  postingsRef.current = postings;
  stateRef.current = input.state;

  const loadMore = useCallback(async () => {
    const current = stateRef.current;
    if (!current?.enabled || !input.scopeReady || loadingRef.current) return;
    const generation = generationRef.current;
    const demandOffset = cursorRef.current;
    const demandTarget = Math.min(
      10_000,
      demandOffset + AI_FILTER_PREFETCH_CANDIDATES,
    );
    loadingRef.current = true;
    setIsLoading(true);
    setUnavailable(false);
    try {
      await requestAiFilterDemand(input.watchlistId, demandOffset);
      let collected = 0;
      let nextHasMore = true;
      for (let attempt = 0; attempt < AI_FILTER_POLL_ATTEMPTS; attempt += 1) {
        if (generationRef.current !== generation) return;
        const nextState = await readAiFilterState(input.watchlistId);
        stateRef.current = nextState;
        input.onStateChange?.(nextState);
        setTotal(nextState.counts.accepted);
        setEvaluated(nextState.counts.total);

        const page = await readAcceptedPage(
          input.watchlistId,
          cursorRef.current,
          Math.max(1, BATCH - collected),
        );
        if (generationRef.current !== generation) return;
        cursorRef.current = page.nextOffset;
        nextHasMore = page.hasMore;
        if (page.decisions.length > 0) {
          const seen = new Set(postingsRef.current.map((posting) => posting.id));
          const fresh = page.decisions
            .map((decision) => decision.posting)
            .filter((posting) => !seen.has(posting.id));
          if (fresh.length > 0) {
            postingsRef.current = [...postingsRef.current, ...fresh];
            setPostings(postingsRef.current);
            collected += fresh.length;
          }
        }

        const coveredOffset = nextState.progress.selectionOffset +
          nextState.progress.scannedCount;
        if (
          collected >= BATCH ||
          !nextHasMore ||
          isAiFilterTerminal(nextState) ||
          coveredOffset >= demandTarget
        ) {
          setHasMore(nextHasMore && !isAiFilterTerminal(nextState));
          return;
        }
        await waitForAiFilterPoll();
      }
      setHasMore(nextHasMore);
    } catch (error) {
      setUnavailable(true);
      logExternalError(
        "warn",
        { service: "external_http", operation: "watchlist_matching_results" },
        error,
      );
    } finally {
      if (generationRef.current === generation) {
        loadingRef.current = false;
        setIsLoading(false);
      }
    }
  }, [input.onStateChange, input.scopeReady, input.watchlistId]);

  const queryVersionId = input.state?.queryVersionId ?? null;
  const enabled = input.state?.enabled === true;
  useEffect(() => {
    generationRef.current += 1;
    const nextPostings = input.initialPage?.postings ?? [];
    postingsRef.current = nextPostings;
    cursorRef.current = input.initialPage?.nextOffset ?? 0;
    stateRef.current = input.state;
    loadingRef.current = false;
    setPostings(nextPostings);
    setTotal(input.state?.counts.accepted ?? nextPostings.length);
    setEvaluated(input.state?.counts.total ?? 0);
    setHasMore(input.initialPage?.hasMore ?? Boolean(input.state?.enabled));
    setIsLoading(false);
    setUnavailable(false);
    if (enabled && input.scopeReady && nextPostings.length === 0) {
      void loadMore();
    }
    // A hard-filter edit deliberately invalidates visible decisions before
    // the debounced watchlist mutation reaches the server. Once `scopeReady`
    // flips back to true, the same effect starts reconciliation against the
    // persisted scope. The initial page only belongs to the original key.
  }, [
    enabled,
    input.initialPage,
    input.scopeKey,
    input.scopeReady,
    loadMore,
    queryVersionId,
  ]);

  return {
    postings,
    total: Math.max(total, postings.length),
    evaluated,
    hasMore,
    isLoading,
    unavailable,
    loadMore,
  };
}

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
  yearTotal,
  initialSearchUnavailable = false,
  jobLanguages,
  locale,
  onResultStateChange,
  aiFilterState = null,
  initialAiAcceptedPage = null,
  onAiFilterStateChange,
  aiFilterScopeKey = "",
  aiFilterScopeReady = true,
}: {
  filters: WatchlistJobListFilters;
  initialPostings: WatchlistPostingEntry[];
  initialTotal: number;
  yearTotal: number;
  initialSearchUnavailable?: boolean;
  jobLanguages: string[];
  locale: string;
  onResultStateChange?: (state: {
    candidateCount: number | undefined;
    unavailable: boolean;
  }) => void;
  aiFilterState?: AiFilterUiState | null;
  initialAiAcceptedPage?: AiFilterAcceptedPage | null;
  onAiFilterStateChange?: (state: AiFilterUiState) => void;
  aiFilterScopeKey?: string;
  aiFilterScopeReady?: boolean;
}) {
  const { i18n, t } = useLingui();
  const { isLoggedIn } = useSession();
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
    batchSize: BATCH,
    itemKey: (p) => p.id,
    resetKey: filtersKey,
    fetcher: async ({ offset, limit }) => {
      try {
        return await runGetWatchlistPostings(
          { ...filtersRef.current, offset, limit },
          isLoggedInRef.current,
        );
      } catch (error) {
        setSearchUnavailable(true);
        throw error;
      }
    },
  });
  const aiResults = useAiFilteredResults({
    watchlistId: aiFilterState?.watchlistId ?? "",
    state: aiFilterState,
    initialPage: initialAiAcceptedPage,
    scopeKey: aiFilterScopeKey,
    scopeReady: aiFilterScopeReady,
    onStateChange: onAiFilterStateChange,
  });
  const aiFilterActive = aiFilterState?.enabled === true;
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
    if (filtersKey === initialFiltersKeyRef.current) return;
    setSearchUnavailable(false);
    let cancelled = false;
    runGetWatchlistPostingYearCount(filtersRef.current).then((next) => {
      if (cancelled) return;
      setYearTotal(next);
    }).catch((err) => {
      setSearchUnavailable(true);
      logExternalError("error", { service: "typesense", operation: "watchlist_year_count" }, err);
    });
    return () => {
      cancelled = true;
    };
  }, [filtersKey]);

  const { sentinelRef, isLoading: isNormalLoading } = useInfiniteScroll({
    hasMore: normalResults.hasMore,
    load: normalResults.loadMore,
  });
  const isLoading = aiFilterActive ? aiResults.isLoading : isNormalLoading;
  const showUnavailable = aiFilterActive
    ? aiResults.unavailable || aiFilterState.status === "provider_unavailable" ||
      aiFilterState.status === "paused_kill" || aiFilterState.status === "failed" ||
      aiFilterState.status === "paused_budget" ||
      aiFilterState.status === "paused_entitlement"
    : searchUnavailable || (
      postings.length === 0 && !isLoading && total > 0
  );

  useEffect(() => {
    onResultStateChange?.({
      candidateCount: searchUnavailable ? undefined : normalResults.total,
      unavailable: searchUnavailable,
    });
  }, [
    onResultStateChange,
    normalResults.resultRevision,
    normalResults.total,
    searchUnavailable,
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
        className={`relative flex min-h-10 w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors [contain:layout] hover:bg-border-soft ${
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

        <span className="shrink-0 text-xs text-muted">
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
    values: { count: aiResults.evaluated },
  });
  const listColumn = (
    <div className="space-y-4">
      {!searchUnavailable && !aiFilterActive && (
        <LanguageStatsRow
          jobLanguages={jobLanguages}
          locale={locale}
          activeCount={total}
          yearCount={yearTotal_}
        />
      )}
      {aiFilterActive && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
          <span>
            <Trans
              id="watchlists.jobList.preciseResults"
              comment="Status label above a watchlist narrowed by natural-language matching"
            >
              Matching results
            </Trans>
          </span>
          <span className="tabular-nums">
            {acceptedCountLabel} · {evaluatedCountLabel}
          </span>
        </div>
      )}
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

        {showUnavailable ? (
          <SearchUnavailable />
        ) : aiFilterActive && postings.length === 0 && isLoading ? (
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

        {aiFilterActive && aiResults.hasMore && !showUnavailable ? (
          <div className="flex justify-center py-5">
            <button
              type="button"
              onClick={() => void aiResults.loadMore()}
              disabled={aiResults.isLoading}
              className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border-soft px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-primary/30 hover:text-foreground disabled:cursor-wait disabled:opacity-60"
            >
              {aiResults.isLoading ? (
                <Loader2 size={14} className="motion-safe:animate-spin" aria-hidden="true" />
              ) : null}
              <Trans
                id="watchlists.jobList.reviewMore"
                comment="Button that evaluates the next portion of a narrowed watchlist on demand"
              >
                Review more jobs
              </Trans>
            </button>
          </div>
        ) : null}
        {!aiFilterActive && normalResults.hasMore && (
          <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} />
        )}
        {!aiFilterActive && !normalResults.hasMore && normalResults.truncated && (
          <TruncationPrompt type="postings" />
        )}
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
          <MobileJobDetailDialog postingId={showPostingId} onClose={handleClosePosting} />
        </>
      )}
    </div>
  );
}
