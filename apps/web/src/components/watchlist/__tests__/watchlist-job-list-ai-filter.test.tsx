import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const hookMocks = vi.hoisted(() => ({
  infiniteOptions: [] as Array<{
    hasMore: boolean;
    load: () => Promise<void>;
  }>,
  paginatedFetchers: [] as Array<(params: {
    offset: number;
    limit: number;
  }) => Promise<unknown>>,
  paginatedHasMore: false,
  session: { isLoggedIn: true, isPending: false },
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/CompanyIcon", () => ({
  CompanyIcon: ({ alt }: { alt: string }) => <span>{alt}</span>,
}));
vi.mock("@/lib/time", () => ({ timeAgoShort: () => "1h" }));
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => hookMocks.session,
}));
vi.mock("@/components/providers/SavedJobsProvider", () => ({
  useSavedJobs: () => ({ isSaved: () => false, toggle: vi.fn() }),
}));
vi.mock("@/components/search/job-detail-dialog", () => ({
  JobDetailPanel: () => null,
}));
vi.mock("@/components/search/mobile-job-detail-dialog", () => ({
  MobileJobDetailDialog: () => null,
}));
vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: (options: { hasMore: boolean; load: () => Promise<void> }) => {
    hookMocks.infiniteOptions.push(options);
    return { sentinelRef: vi.fn(), isLoading: false };
  },
}));
vi.mock("@/lib/use-paginated-load-more", () => ({
  usePaginatedLoadMore: ({ initialItems, initialTotal, fetcher }: {
    initialItems: unknown[];
    initialTotal: number;
    fetcher: (params: { offset: number; limit: number }) => Promise<unknown>;
  }) => ({
    ...(hookMocks.paginatedFetchers.push(fetcher), {}),
    items: initialItems,
    total: initialTotal,
    truncated: false,
    hasMore: hookMocks.paginatedHasMore,
    loadMore: vi.fn(),
    resultRevision: 0,
  }),
}));
vi.mock("@/components/InfiniteScrollSentinel", () => ({
  InfiniteScrollSentinel: () => <div data-testid="infinite-scroll-sentinel" />,
}));
vi.mock("@/components/TruncationPrompt", () => ({
  TruncationPrompt: () => <div>Sign in to see more job postings</div>,
}));
vi.mock("@/components/TrackingDot", () => ({ TrackingDot: () => null }));
vi.mock("@/components/PendingJobWarning", () => ({ PendingJobIcon: () => null }));
vi.mock("@/components/search/language-stats-row", () => ({
  LanguageStatsRow: () => <div>Ordinary result stats</div>,
}));
vi.mock("@/components/search/search-unavailable", () => ({
  SearchUnavailable: () => <div role="alert">Search unavailable</div>,
}));
vi.mock("@/components/watchlist/format-date-divider", () => ({
  formatDateDivider: () => "Today",
  getDateKey: (value: string) => value,
}));
vi.mock("@/lib/search/search-runner", () => ({
  runGetWatchlistPostings: vi.fn(),
  runGetWatchlistPostingYearCount: vi.fn(),
}));
vi.mock("@/lib/safe-external-error", () => ({ logExternalError: vi.fn() }));

import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";
import type { AiFilterUiState } from "@/lib/ai-filter/ui-contract";
import { runGetWatchlistPostings } from "@/lib/search/search-runner";
import { WatchlistJobList } from "../watchlist-job-list";

const watchlistId = "11111111-1111-4111-8111-111111111111";

function posting(id: string, title: string): WatchlistPostingEntry {
  return {
    id,
    title,
    sourceUrl: `https://example.com/${id}`,
    firstSeenAt: "2026-09-21T10:00:00.000Z",
    isActive: true,
    company: { id: "company-1", name: "Example", slug: "example", icon: null },
  };
}

function aiState(overrides: Partial<AiFilterUiState> = {}): AiFilterUiState {
  return {
    watchlistId,
    enabled: true,
    entitled: true,
    query: "Backend roles without management",
    queryRevision: 1,
    queryVersionId: "22222222-2222-4222-8222-222222222222",
    status: "processing",
    counts: { accepted: 0, rejected: 0, total: 0 },
    progress: { selectionOffset: 0, scannedCount: 0, completedCount: 0, stopReason: null },
    lastCaughtUpAt: null,
    latestEventSequence: 1,
    ...overrides,
  };
}

function reconcileResponse(state: AiFilterUiState = aiState()) {
  return {
    ok: true,
    json: async () => ({ state, workflow: null }),
  } as Response;
}

const baseProps = {
  filters: { companyIds: ["company-1"] },
  initialPostings: [posting("raw", "Raw candidate")],
  initialTotal: 1,
  yearTotal: 1,
  jobLanguages: ["en"],
  locale: "en",
};

describe("WatchlistJobList matching results", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    hookMocks.infiniteOptions = [];
    hookMocks.paginatedFetchers = [];
    hookMocks.paginatedHasMore = false;
    hookMocks.session = { isLoggedIn: true, isPending: false };
  });

  it("waits for signed-in session hydration before broad pagination", () => {
    hookMocks.paginatedHasMore = true;
    hookMocks.session = { isLoggedIn: false, isPending: true };
    const { rerender } = render(
      <WatchlistJobList
        {...baseProps}
        initialTotal={50}
        resultMode="broad"
      />,
    );

    expect(hookMocks.infiniteOptions.at(-1)?.hasMore).toBe(false);

    hookMocks.session = { isLoggedIn: true, isPending: false };
    rerender(
      <WatchlistJobList
        {...baseProps}
        initialTotal={50}
        resultMode="broad"
      />,
    );

    expect(hookMocks.infiniteOptions.at(-1)?.hasMore).toBe(true);
  });

  it("keeps a public snapshot capped until anonymous session state settles", async () => {
    hookMocks.paginatedHasMore = true;
    hookMocks.session = { isLoggedIn: false, isPending: true };
    vi.mocked(runGetWatchlistPostings).mockResolvedValue({
      postings: [],
      total: 50,
      truncated: false,
    });

    const { rerender } = render(
      <WatchlistJobList
        {...baseProps}
        initialTotal={50}
        resultMode="broad"
        sharedSnapshot
      />,
    );

    expect(hookMocks.infiniteOptions.at(-1)?.hasMore).toBe(false);

    hookMocks.session = { isLoggedIn: false, isPending: false };
    rerender(
      <WatchlistJobList
        {...baseProps}
        initialTotal={50}
        resultMode="broad"
        sharedSnapshot
      />,
    );
    expect(hookMocks.infiniteOptions.at(-1)?.hasMore).toBe(true);
    const fetchPage = hookMocks.paginatedFetchers.at(-1);
    await fetchPage!({ offset: 20, limit: 20 });
    expect(runGetWatchlistPostings).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 20, limit: 20 }),
      false,
    );
  });

  it("prompts an anonymous viewer after the narrowed-feed limit", () => {
    hookMocks.session = { isLoggedIn: false, isPending: false };
    const accepted = Array.from(
      { length: 20 },
      (_, index) => posting(`accepted-${index}`, `Accepted ${index}`),
    );

    render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={aiState({
          status: "caught_up",
          counts: { accepted: 30, rejected: 20, total: 50 },
        })}
        initialAiAcceptedPage={{
          queryVersionId: "22222222-2222-4222-8222-222222222222",
          postings: accepted,
          total: 30,
          nextOffset: 50,
          hasMore: true,
        }}
        aiFilterReadOnly
      />,
    );

    expect(hookMocks.infiniteOptions.at(-1)?.hasMore).toBe(false);
    expect(screen.getByText("Sign in to see more job postings")).toBeTruthy();
  });

  it("keeps broad results and stats when a later page fails", async () => {
    vi.mocked(runGetWatchlistPostings).mockRejectedValueOnce(
      new Error("temporary search failure"),
    );

    render(
      <WatchlistJobList
        {...baseProps}
        initialTotal={50}
        resultMode="broad"
      />,
    );

    const fetchPage = hookMocks.paginatedFetchers.at(-1);
    expect(fetchPage).toBeTruthy();
    await act(async () => {
      await expect(fetchPage!({ offset: 20, limit: 20 })).rejects.toThrow(
        "temporary search failure",
      );
    });

    expect(screen.getByText("Raw candidate")).toBeTruthy();
    expect(screen.getByText("Ordinary result stats")).toBeTruthy();
    const statsRow = document.getElementById("watchlist-results-boundary");
    expect(statsRow?.className).toContain("bg-background/80");
    expect(statsRow?.className).toContain("backdrop-blur-md");
    expect(statsRow?.className).not.toContain("bg-surface-alpha");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("hides raw candidates and replaces them with accepted decisions", async () => {
    const accepted = posting("accepted", "Accepted role");
    const completed = aiState({
      status: "caught_up",
      counts: { accepted: 1, rejected: 2, total: 3 },
      progress: { selectionOffset: 0, scannedCount: 3, completedCount: 3, stopReason: "caught_up" },
    });
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (init?.method === "POST") return reconcileResponse(completed);
      if (url.includes("/decisions?")) {
        return {
          ok: true,
          json: async () => ({
            decisions: [{ posting: accepted }],
            nextOffset: 3,
            hasMore: false,
          }),
        } as Response;
      }
      return { ok: true, json: async () => completed } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <WatchlistJobList
        {...baseProps}
        aiFilterState={aiState()}
        initialAiAcceptedPage={null}
        aiFilterScopeKey="scope-1"
      />,
    );

    expect(screen.queryByText("Raw candidate")).toBeNull();
    expect(screen.getByText("Ordinary result stats")).toBeTruthy();
    expect(screen.getByText("Reviewing this feed…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Review more jobs" })).toBeNull();

    expect(await screen.findByText("Accepted role")).toBeTruthy();
    expect(screen.getByText("Matching results")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/api/web/watchlists/${watchlistId}/ai-filter/decisions?`),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST"))
      .toBe(false);
  });

  it("restores persisted results immediately while maintaining the runway", async () => {
    const accepted = posting("accepted", "Persisted accepted role");
    const restoredState = aiState({
      status: "processing",
      counts: { accepted: 1, rejected: 2, total: 3 },
    });
    const fetchMock = vi.fn(async () => reconcileResponse(restoredState));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistJobList
        {...baseProps}
        aiFilterState={restoredState}
        initialAiAcceptedPage={{
          postings: [accepted],
          nextOffset: 3,
          hasMore: true,
          queryVersionId: restoredState.queryVersionId,
        }}
        aiFilterScopeKey="scope-1"
      />,
    );

    expect(screen.getByText("Persisted accepted role")).toBeTruthy();
    expect(screen.getByTestId("infinite-scroll-sentinel")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Review more jobs" })).toBeNull();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/web/watchlists/${watchlistId}/ai-filter/reconcile`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.queryByText("Reviewing this feed…")).toBeNull();
  });

  it("refreshes counters when background evaluation finishes after rows load", async () => {
    const accepted = posting("accepted", "Persisted accepted role");
    const processing = aiState({
      status: "processing",
      counts: { accepted: 1, rejected: 2, total: 3 },
    });
    const completed = aiState({
      status: "caught_up",
      counts: { accepted: 2, rejected: 2, total: 4 },
      progress: {
        selectionOffset: 0,
        scannedCount: 4,
        completedCount: 4,
        stopReason: "caught_up",
      },
    });
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ state: processing, workflow: null }),
        } as Response;
      }
      return { ok: true, json: async () => completed } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    const onStateChange = vi.fn();

    render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={processing}
        initialAiAcceptedPage={{
          postings: [accepted],
          nextOffset: 3,
          hasMore: true,
          queryVersionId: processing.queryVersionId,
        }}
        aiFilterScopeKey="scope-1"
        onAiFilterStateChange={onStateChange}
      />,
    );

    await waitFor(() => {
      expect(onStateChange).toHaveBeenCalledWith(completed);
    }, { timeout: 2_000 });
  });

  it("appends newly evaluated matches through the infinite-scroll loader", async () => {
    const first = posting("accepted-1", "First accepted role");
    const second = posting("accepted-2", "Newly accepted role");
    const nextState = aiState({
      counts: { accepted: 2, rejected: 2, total: 4 },
      progress: { selectionOffset: 0, scannedCount: 4, completedCount: 4, stopReason: null },
    });
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (init?.method === "POST") {
        return reconcileResponse(nextState);
      }
      if (url.includes("/decisions?")) {
        return {
          ok: true,
          json: async () => ({
            decisions: [{ posting: second }],
            nextOffset: 4,
            hasMore: false,
          }),
        } as Response;
      }
      return { ok: true, json: async () => nextState } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistJobList
        {...baseProps}
        aiFilterState={aiState({
          counts: { accepted: 1, rejected: 2, total: 3 },
          progress: { selectionOffset: 0, scannedCount: 3, completedCount: 3, stopReason: null },
        })}
        initialAiAcceptedPage={{
          postings: [first],
          nextOffset: 3,
          hasMore: true,
        }}
        aiFilterScopeKey="scope-1"
      />,
    );

    const infinite = hookMocks.infiniteOptions.findLast((options) => options.hasMore);
    expect(infinite).toBeTruthy();
    await act(async () => {
      await infinite!.load();
    });

    expect(screen.getByText("First accepted role")).toBeTruthy();
    expect(screen.getByText("Newly accepted role")).toBeTruthy();
  });

  it("pages a shared narrowed feed without starting or polling evaluation", async () => {
    const first = posting("shared-1", "First shared match");
    const second = posting("shared-2", "Second shared match");
    const fetchMock = vi.fn(async (_request: RequestInfo | URL) => ({
      ok: true,
      json: async () => ({
        decisions: [{ posting: second }],
        total: 2,
        nextOffset: 2,
        hasMore: false,
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={aiState({
          status: "caught_up",
          counts: { accepted: 2, rejected: 8, total: 10 },
        })}
        initialAiAcceptedPage={{
          postings: [first],
          total: 2,
          nextOffset: 1,
          hasMore: true,
        }}
        aiFilterScopeKey="shared-scope"
        aiFilterReadOnly
      />,
    );

    const infinite = hookMocks.infiniteOptions.findLast((options) => options.hasMore);
    expect(infinite).toBeTruthy();
    await act(async () => {
      await infinite!.load();
    });

    expect(screen.getByText("First shared match")).toBeTruthy();
    expect(screen.getByText("Second shared match")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/ai-filter/decisions?");
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("/reconcile");
  });

  it("continues paging persisted matches after evaluation is caught up", async () => {
    const first = posting("accepted-0", "Accepted role 0");
    const nextTwenty = Array.from({ length: 20 }, (_, index) =>
      posting(`accepted-${index + 1}`, `Accepted role ${index + 1}`));
    const finalPosting = posting("accepted-21", "Accepted role 21");
    const caughtUp = aiState({
      status: "caught_up",
      counts: { accepted: 22, rejected: 8, total: 30 },
      progress: { selectionOffset: 0, scannedCount: 30, completedCount: 30, stopReason: "caught_up" },
    });
    let decisionRequest = 0;
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (init?.method === "POST") {
        return reconcileResponse(caughtUp);
      }
      if (url.includes("/decisions?")) {
        decisionRequest += 1;
        return {
          ok: true,
          json: async () => decisionRequest === 1
            ? {
                decisions: nextTwenty.map((entry) => ({ posting: entry })),
                nextOffset: 21,
                hasMore: true,
              }
            : {
                decisions: [{ posting: finalPosting }],
                nextOffset: 30,
                hasMore: false,
              },
        } as Response;
      }
      return { ok: true, json: async () => caughtUp } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={caughtUp}
        initialAiAcceptedPage={{
          postings: [first],
          nextOffset: 1,
          hasMore: true,
        }}
        aiFilterScopeKey="scope-1"
      />,
    );

    const firstLoad = hookMocks.infiniteOptions.findLast((options) => options.hasMore);
    expect(firstLoad).toBeTruthy();
    await act(async () => {
      await firstLoad!.load();
    });
    expect(screen.getByText("Accepted role 20")).toBeTruthy();

    const secondLoad = hookMocks.infiniteOptions.findLast((options) => options.hasMore);
    expect(secondLoad).toBeTruthy();
    await act(async () => {
      await secondLoad!.load();
    });
    expect(screen.getByText("Accepted role 21")).toBeTruthy();
  });

  it("finishes a load at an evaluated frontier instead of polling an empty cursor", async () => {
    const first = posting("accepted-0", "Accepted role 0");
    const next = posting("accepted-1", "Accepted role 1");
    // The state read can lag the coverage used by the decisions endpoint by
    // one commit. The advancing decision cursor is the authoritative signal
    // that the currently evaluated window was consumed.
    const staleProgress = aiState({
      status: "processing",
      counts: { accepted: 2, rejected: 298, total: 300 },
      progress: { selectionOffset: 0, scannedCount: 299, completedCount: 299, stopReason: null },
    });
    let decisionRequest = 0;
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (init?.method === "POST") {
        return reconcileResponse(staleProgress);
      }
      if (url.includes("/decisions?")) {
        decisionRequest += 1;
        return {
          ok: true,
          json: async () => ({
            decisions: [{ posting: next }],
            nextOffset: 360,
            hasMore: true,
          }),
        } as Response;
      }
      return { ok: true, json: async () => staleProgress } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={staleProgress}
        initialAiAcceptedPage={{
          postings: [first],
          nextOffset: 206,
          hasMore: true,
        }}
        aiFilterScopeKey="scope-1"
      />,
    );

    const infinite = hookMocks.infiniteOptions.findLast((options) => options.hasMore);
    expect(infinite).toBeTruthy();
    await act(async () => {
      await infinite!.load();
    });

    expect(screen.getByText("Accepted role 1")).toBeTruthy();
    expect(decisionRequest).toBe(2);
    expect(hookMocks.infiniteOptions.findLast((options) => options.hasMore)).toBeTruthy();
  });

  it("does not evaluate the narrowed feed until its drawer is open", async () => {
    const accepted = posting("accepted", "On-demand accepted role");
    const completed = aiState({
      status: "caught_up",
      counts: { accepted: 1, rejected: 0, total: 1 },
      progress: { selectionOffset: 0, scannedCount: 1, completedCount: 1, stopReason: "caught_up" },
    });
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (init?.method === "POST") return reconcileResponse(completed);
      if (url.includes("/decisions?")) {
        return {
          ok: true,
          json: async () => ({
            decisions: [{ posting: accepted }],
            nextOffset: 1,
            hasMore: false,
          }),
        } as Response;
      }
      return { ok: true, json: async () => completed } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={aiState()}
        initialAiAcceptedPage={null}
        aiFilterScopeKey="scope-1"
        aiFilterScopeReady={false}
      />,
    );

    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());

    rerender(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={aiState()}
        initialAiAcceptedPage={null}
        aiFilterScopeKey="scope-1"
        aiFilterScopeReady
      />,
    );

    expect(await screen.findByText("On-demand accepted role")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/api/web/watchlists/${watchlistId}/ai-filter/decisions?`),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST"))
      .toBe(false);
  });

  it("resets a stale candidate cursor when the query revision changes", async () => {
    const stale = posting("stale", "Stale revision result");
    const fresh = posting("fresh", "Fresh revision result");
    const firstRevision = aiState({
      counts: { accepted: 1, rejected: 53, total: 54 },
      progress: { selectionOffset: 0, scannedCount: 54, completedCount: 54, stopReason: null },
    });
    const secondRevision = aiState({
      queryRevision: 2,
      queryVersionId: "33333333-3333-4333-8333-333333333333",
      counts: { accepted: 1, rejected: 49, total: 50 },
      progress: { selectionOffset: 0, scannedCount: 50, completedCount: 50, stopReason: null },
    });
    const decisionUrls: string[] = [];
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ state: secondRevision, workflow: { runId: "run-2" } }),
        } as Response;
      }
      if (url.includes("/decisions?")) {
        decisionUrls.push(url);
        return {
          ok: true,
          json: async () => ({
            decisions: [{ posting: fresh }],
            nextOffset: 50,
            hasMore: false,
          }),
        } as Response;
      }
      return { ok: true, json: async () => secondRevision } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    const stalePage = {
      queryVersionId: firstRevision.queryVersionId,
      postings: [stale],
      nextOffset: 54,
      hasMore: true,
    };
    const { rerender } = render(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={firstRevision}
        initialAiAcceptedPage={stalePage}
        aiFilterScopeKey="scope-1"
      />,
    );

    rerender(
      <WatchlistJobList
        {...baseProps}
        resultMode="narrowed"
        aiFilterState={secondRevision}
        initialAiAcceptedPage={stalePage}
        aiFilterScopeKey="scope-1"
      />,
    );

    expect(await screen.findByText("Fresh revision result")).toBeTruthy();
    expect(screen.queryByText("Stale revision result")).toBeNull();
    expect(decisionUrls.some((url) => url.includes("offset=0"))).toBe(true);
    expect(decisionUrls.some((url) => url.includes("offset=54"))).toBe(false);
  });
});
