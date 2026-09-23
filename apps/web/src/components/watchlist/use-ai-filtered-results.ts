"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";
import type { AiFilterAcceptedPage, AiFilterUiState } from "@/lib/ai-filter/ui-contract";
import {
  AI_FILTER_PREFETCH_CANDIDATES,
  AI_FILTER_RESULT_PAGE_SIZE,
} from "@/lib/ai-filter/demand";
import { logExternalError } from "@/lib/safe-external-error";

const BATCH = AI_FILTER_RESULT_PAGE_SIZE;
const AI_FILTER_POLL_MS = 1_500;
const AI_FILTER_POLL_ATTEMPTS = 30;
const AI_FILTER_BACKGROUND_POLL_MS = 5_000;
const AI_FILTER_BACKGROUND_POLL_ATTEMPTS = 24;

type AiDecisionPageResponse = {
  decisions: Array<{ posting: WatchlistPostingEntry }>;
  total?: number;
  nextOffset: number;
  hasMore: boolean;
  state?: AiFilterUiState;
};

function aiFilterResultsBlocked(state: AiFilterUiState): boolean {
  return state.status === "paused_entitlement" ||
    state.status === "paused_budget" ||
    state.status === "provider_unavailable" ||
    state.status === "paused_kill" ||
    state.status === "cancelled" ||
    state.status === "failed" ||
    state.status === "disabled";
}

function aiFilterDemandCannotStart(state: AiFilterUiState): boolean {
  return state.status === "paused_entitlement" ||
    state.status === "paused_budget" ||
    state.status === "cancelled" ||
    state.status === "failed" ||
    state.status === "disabled";
}

async function readAiFilterState(
  watchlistId: string,
  signal?: AbortSignal,
): Promise<AiFilterUiState> {
  const response = await fetch(`/api/web/watchlists/${watchlistId}/ai-filter`, {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error("matching_state_unavailable");
  return response.json() as Promise<AiFilterUiState>;
}

async function readAcceptedPage(
  watchlistId: string,
  offset: number,
  limit: number,
  signal?: AbortSignal,
): Promise<AiDecisionPageResponse> {
  const params = new URLSearchParams({
    bucket: "accepted",
    offset: String(offset),
    limit: String(limit),
  });
  const response = await fetch(
    `/api/web/watchlists/${watchlistId}/ai-filter/decisions?${params}`,
    { credentials: "same-origin", cache: "no-store", signal },
  );
  if (!response.ok) throw new Error("matching_results_unavailable");
  return response.json() as Promise<AiDecisionPageResponse>;
}

async function requestAiFilterDemand(
  watchlistId: string,
  offset: number,
  jobLanguages: readonly string[],
  locale: string,
): Promise<{
  state: AiFilterUiState;
  workflow: { runId: string } | null;
}> {
  const response = await fetch(
    `/api/web/watchlists/${watchlistId}/ai-filter/reconcile`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offset, jobLanguages, locale }),
    },
  );
  if (!response.ok) throw new Error("matching_reconcile_unavailable");
  return response.json() as Promise<{
    state: AiFilterUiState;
    workflow: { runId: string } | null;
  }>;
}

function waitForAiFilterPoll(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, AI_FILTER_POLL_MS));
}

function waitForVisibleTab(signal: AbortSignal): Promise<void> {
  if (!document.hidden) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      document.removeEventListener("visibilitychange", onVisible);
      signal.removeEventListener("abort", onAbort);
    };
    const onVisible = () => {
      if (document.hidden) return;
      cleanup();
      resolve();
    };
    const onAbort = () => {
      cleanup();
      reject(new Error("AI filter read was cancelled"));
    };
    document.addEventListener("visibilitychange", onVisible);
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
    else if (!document.hidden) onVisible();
  });
}

export function useAiFilteredResults(input: {
  active: boolean;
  watchlistId: string;
  state: AiFilterUiState | null;
  initialPage: AiFilterAcceptedPage | null;
  scopeKey: string;
  scopeReady: boolean;
  jobLanguages: readonly string[];
  locale: string;
  readOnly: boolean;
  onStateChange?: (state: AiFilterUiState) => void;
}) {
  const initialPage = input.initialPage?.queryVersionId &&
      input.initialPage.queryVersionId !== input.state?.queryVersionId
    ? null
    : input.initialPage;
  const [postings, setPostings] = useState<WatchlistPostingEntry[]>(
    initialPage?.postings ?? [],
  );
  const [total, setTotal] = useState(
    initialPage?.total ?? input.state?.counts.accepted ?? 0,
  );
  const [evaluated, setEvaluated] = useState(input.state?.counts.total ?? 0);
  const [hasMore, setHasMore] = useState(initialPage?.hasMore ?? false);
  const [isLoading, setIsLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [visibilityEpoch, setVisibilityEpoch] = useState(0);
  const cursorRef = useRef(initialPage?.nextOffset ?? 0);
  const postingsRef = useRef(postings);
  const stateRef = useRef(input.state);
  const loadingRef = useRef(false);
  const generationRef = useRef(0);
  const prefetchKeyRef = useRef("");
  const pendingScrollTopRef = useRef<number | null>(null);
  const readAbortRef = useRef<AbortController | null>(null);
  postingsRef.current = postings;
  stateRef.current = input.state;

  useLayoutEffect(() => {
    const top = pendingScrollTopRef.current;
    if (top == null) return;
    pendingScrollTopRef.current = null;
    window.scrollTo(window.scrollX, top);
  }, [postings.length]);

  const loadMore = useCallback(async () => {
    const current = stateRef.current;
    if (!input.active || !current?.enabled || !input.scopeReady || loadingRef.current) return;
    const generation = generationRef.current;
    const readAbort = new AbortController();
    readAbortRef.current?.abort();
    readAbortRef.current = readAbort;
    loadingRef.current = true;
    setIsLoading(true);
    setUnavailable(false);
    try {
      await waitForVisibleTab(readAbort.signal);
      if (input.readOnly) {
        const page = await readAcceptedPage(
          input.watchlistId,
          cursorRef.current,
          BATCH,
          readAbort.signal,
        );
        if (generationRef.current !== generation) return;
        cursorRef.current = page.nextOffset;
        if (page.total != null) setTotal(page.total);
        if (page.decisions.length > 0) {
          const seen = new Set(postingsRef.current.map((posting) => posting.id));
          const fresh = page.decisions
            .map((decision) => decision.posting)
            .filter((posting) => !seen.has(posting.id));
          if (fresh.length > 0) {
            pendingScrollTopRef.current = window.scrollY;
            postingsRef.current = [...postingsRef.current, ...fresh];
            setPostings(postingsRef.current);
          }
        }
        setHasMore(page.hasMore);
        return;
      }

      const initialPageOffset = cursorRef.current;
      const initialPage = await readAcceptedPage(
        input.watchlistId,
        initialPageOffset,
        BATCH,
        readAbort.signal,
      );
      if (generationRef.current !== generation) return;
      cursorRef.current = initialPage.nextOffset;
      if (initialPage.state) {
        stateRef.current = initialPage.state;
        input.onStateChange?.(initialPage.state);
        setEvaluated(initialPage.state.counts.total);
      }
      if (initialPage.total != null) setTotal(initialPage.total);
      const initialSeen = new Set(
        postingsRef.current.map((posting) => posting.id),
      );
      const initialFresh = initialPage.decisions
        .map((decision) => decision.posting)
        .filter((posting) => !initialSeen.has(posting.id));
      if (initialFresh.length > 0) {
        pendingScrollTopRef.current = window.scrollY;
        postingsRef.current = [...postingsRef.current, ...initialFresh];
        setPostings(postingsRef.current);
      }
      if (initialFresh.length >= BATCH || !initialPage.hasMore) {
        setHasMore(initialPage.hasMore);
        return;
      }

      const latest = stateRef.current ?? current;
      const latestDemandOffset = latest.progress.selectionOffset +
        latest.progress.scannedCount;
      const latestDemandTarget = Math.min(
        10_000,
        latestDemandOffset + AI_FILTER_PREFETCH_CANDIDATES,
      );
      await waitForVisibleTab(readAbort.signal);
      const demand = await requestAiFilterDemand(
        input.watchlistId,
        latestDemandOffset,
        input.jobLanguages,
        input.locale,
      );
      const demandBaseline = demand.state;
      stateRef.current = demand.state;
      input.onStateChange?.(demand.state);
      setTotal(demand.state.counts.accepted);
      setEvaluated(demand.state.counts.total);
      let collected = initialFresh.length;
      let nextHasMore: boolean = initialPage.hasMore;
      for (let attempt = 0; attempt < AI_FILTER_POLL_ATTEMPTS; attempt += 1) {
        if (generationRef.current !== generation) return;
        if (attempt > 0) await waitForAiFilterPoll();
        await waitForVisibleTab(readAbort.signal);
        const pageOffset = cursorRef.current;
        const requestedLimit = Math.max(1, BATCH - collected);
        const page = await readAcceptedPage(
          input.watchlistId,
          pageOffset,
          requestedLimit,
          readAbort.signal,
        );
        if (generationRef.current !== generation) return;
        const nextState: AiFilterUiState =
          page.state ?? stateRef.current ?? demand.state;
        stateRef.current = nextState;
        input.onStateChange?.(nextState);
        setTotal(nextState.counts.accepted);
        setEvaluated(nextState.counts.total);
        cursorRef.current = page.nextOffset;
        nextHasMore = page.hasMore;
        if (page.total != null) setTotal(page.total);
        if (page.decisions.length > 0) {
          const seen = new Set(postingsRef.current.map((posting) => posting.id));
          const fresh = page.decisions
            .map((decision) => decision.posting)
            .filter((posting) => !seen.has(posting.id));
          if (fresh.length > 0) {
            pendingScrollTopRef.current = window.scrollY;
            postingsRef.current = [...postingsRef.current, ...fresh];
            setPostings(postingsRef.current);
            collected += fresh.length;
          }
        }

        const coveredOffset = nextState.progress.selectionOffset +
          nextState.progress.scannedCount;
        // A short page means the client consumed every accepted decision
        // currently persisted after `pageOffset`. Once the requested raw-
        // candidate runway is covered, return control to infinite scroll;
        // polling the same frontier only repeats an empty read.
        const workflowAdvanced =
          nextState.queryVersionId !== demandBaseline.queryVersionId ||
          nextState.latestEventSequence > demandBaseline.latestEventSequence ||
          nextState.counts.total > demandBaseline.counts.total ||
          nextState.progress.selectionOffset !==
            demandBaseline.progress.selectionOffset ||
          nextState.progress.scannedCount !== demandBaseline.progress.scannedCount;
        const currentBlockedState = aiFilterResultsBlocked(nextState) &&
          (demand.workflow === null || workflowAdvanced);
        if (
          collected >= BATCH ||
          !nextHasMore ||
          currentBlockedState ||
          (
            page.decisions.length < requestedLimit &&
            (demand.workflow === null || coveredOffset >= latestDemandTarget)
          )
        ) {
          // `caught_up` means evaluation reached the end of the candidate
          // feed, not that the client consumed every persisted decision.
          // Keep paging accepted decisions until the decision cursor itself
          // reports no remaining candidates.
          setHasMore(nextHasMore && !currentBlockedState);
          return;
        }
      }
      setHasMore(nextHasMore);
    } catch (error) {
      if (readAbort.signal.aborted) return;
      setUnavailable(true);
      logExternalError(
        "warn",
        { service: "external_http", operation: "watchlist_matching_results" },
        error,
      );
    } finally {
      if (generationRef.current === generation) {
        if (readAbortRef.current === readAbort) readAbortRef.current = null;
        loadingRef.current = false;
        setIsLoading(false);
      }
    }
  }, [
    input.active,
    input.jobLanguages,
    input.locale,
    input.onStateChange,
    input.readOnly,
    input.scopeReady,
    input.watchlistId,
  ]);

  const queryVersionId = input.state?.queryVersionId ?? null;
  const enabled = input.state?.enabled === true;

  useEffect(() => {
    if (!input.active) return;
    const onVisibilityChange = () => {
      if (!document.hidden) setVisibilityEpoch((epoch) => epoch + 1);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [input.active]);
  useEffect(() => () => readAbortRef.current?.abort(), []);
  useEffect(() => {
    generationRef.current += 1;
    readAbortRef.current?.abort();
    readAbortRef.current = null;
    const nextPostings = initialPage?.postings ?? [];
    postingsRef.current = nextPostings;
    cursorRef.current = initialPage?.nextOffset ?? 0;
    stateRef.current = input.state;
    loadingRef.current = false;
    setPostings(nextPostings);
    setTotal(initialPage?.total ?? input.state?.counts.accepted ?? nextPostings.length);
    setEvaluated(input.state?.counts.total ?? 0);
    setHasMore(initialPage?.hasMore ?? Boolean(input.state?.enabled));
    setIsLoading(false);
    setUnavailable(false);
    // A hard-filter edit deliberately invalidates visible decisions before
    // the debounced watchlist mutation reaches the server. Once `scopeReady`
    // flips back to true, the effect below starts reconciliation against the
    // persisted scope. The initial page only belongs to the original key.
  }, [
    input.active,
    enabled,
    initialPage,
    input.scopeKey,
    queryVersionId,
  ]);

  useEffect(() => {
    if (
      input.active &&
      enabled &&
      input.scopeReady &&
      postingsRef.current.length === 0 &&
      !loadingRef.current
    ) {
      void loadMore();
    }
  }, [enabled, input.active, input.scopeReady, loadMore, queryVersionId]);

  // Once any matches are visible, maintain the candidate runway in the
  // background. This covers both restored drawers and a fresh query whose
  // first foreground poll returned before the full runway was evaluated; in
  // either case the sentinel may remain intersecting and never fire again.
  // Counts update as durable segments land without keeping a spinner visible.
  useEffect(() => {
    if (
      !input.active ||
      !enabled ||
      input.readOnly ||
      !input.scopeReady ||
      isLoading ||
      postings.length === 0 ||
      !hasMore ||
      document.hidden ||
      aiFilterDemandCannotStart(stateRef.current!)
    ) {
      return;
    }
    const offset = stateRef.current!.progress.selectionOffset +
      stateRef.current!.progress.scannedCount;
    const key = `${queryVersionId}:${input.scopeKey}:${cursorRef.current}:${postings.length}`;
    if (prefetchKeyRef.current === key) return;
    prefetchKeyRef.current = key;
    let cancelled = false;

    void (async () => {
      try {
        const demand = await requestAiFilterDemand(
          input.watchlistId,
          offset,
          input.jobLanguages,
          input.locale,
        );
        if (cancelled) return;
        stateRef.current = demand.state;
        input.onStateChange?.(demand.state);
        setTotal(demand.state.counts.accepted);
        setEvaluated(demand.state.counts.total);
      } catch (error) {
        if (cancelled) return;
        prefetchKeyRef.current = "";
        logExternalError(
          "warn",
          { service: "external_http", operation: "watchlist_matching_prefetch" },
          error,
        );
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    input.active,
    enabled,
    hasMore,
    input.onStateChange,
    input.readOnly,
    input.jobLanguages,
    input.locale,
    input.scopeKey,
    input.scopeReady,
    input.watchlistId,
    isLoading,
    postings.length,
    queryVersionId,
    visibilityEpoch,
  ]);

  // A foreground page can fill with accepted rows before the durable
  // workflow finishes evaluating its candidate runway. Keep the compact
  // counter current while the drawer is open; otherwise it can remain stuck
  // on the state observed when the last visible page completed even though
  // later segments have already committed in the background.
  useEffect(() => {
    if (
      !input.active ||
      input.readOnly ||
      !enabled ||
      !input.scopeReady ||
      !input.watchlistId ||
      isLoading
    ) return;
    if (
      stateRef.current?.status !== "processing" &&
      stateRef.current?.status !== "waiting_for_jev"
    ) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let attempts = 0;
    const poll = async () => {
      if (cancelled) return;
      // A background tab cannot show progress. Wait until it is visible
      // before paying for another state read.
      if (document.visibilityState === "hidden") {
        timer = setTimeout(() => void poll(), AI_FILTER_BACKGROUND_POLL_MS);
        return;
      }
      try {
        controller = new AbortController();
        const nextState = await readAiFilterState(
          input.watchlistId,
          controller.signal,
        );
        if (cancelled) return;
        stateRef.current = nextState;
        input.onStateChange?.(nextState);
        setTotal(nextState.counts.accepted);
        setEvaluated(nextState.counts.total);
        if (
          nextState.status !== "processing" &&
          nextState.status !== "waiting_for_jev"
        ) {
          return;
        }
        attempts += 1;
        if (attempts >= AI_FILTER_BACKGROUND_POLL_ATTEMPTS) return;
      } catch (error) {
        if (cancelled) return;
        logExternalError(
          "warn",
          { service: "external_http", operation: "watchlist_matching_state_poll" },
          error,
        );
      }
      timer = setTimeout(() => void poll(), AI_FILTER_BACKGROUND_POLL_MS);
    };
    timer = setTimeout(() => void poll(), AI_FILTER_POLL_MS);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer) clearTimeout(timer);
    };
  }, [
    input.active,
    enabled,
    input.onStateChange,
    input.readOnly,
    input.scopeReady,
    input.state?.status,
    input.watchlistId,
    isLoading,
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
