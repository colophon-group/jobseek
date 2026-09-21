import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/CompanyIcon", () => ({
  CompanyIcon: ({ alt }: { alt: string }) => <span>{alt}</span>,
}));
vi.mock("@/lib/time", () => ({ timeAgoShort: () => "1h" }));
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: true, isPending: false }),
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
  useInfiniteScroll: () => ({ sentinelRef: { current: null }, isLoading: false }),
}));
vi.mock("@/lib/use-paginated-load-more", () => ({
  usePaginatedLoadMore: ({ initialItems, initialTotal }: {
    initialItems: unknown[];
    initialTotal: number;
  }) => ({
    items: initialItems,
    total: initialTotal,
    truncated: false,
    hasMore: false,
    loadMore: vi.fn(),
    resultRevision: 0,
  }),
}));
vi.mock("@/components/InfiniteScrollSentinel", () => ({
  InfiniteScrollSentinel: () => null,
}));
vi.mock("@/components/TruncationPrompt", () => ({
  TruncationPrompt: () => null,
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
      if (init?.method === "POST") return { ok: true, json: async () => ({}) } as Response;
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
    expect(screen.queryByText("Ordinary result stats")).toBeNull();
    expect(screen.getByText("Reviewing this feed…")).toBeTruthy();

    expect(await screen.findByText("Accepted role")).toBeTruthy();
    expect(screen.getByText("Matching results")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/web/watchlists/${watchlistId}/ai-filter/reconcile`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("restores persisted accepted results without re-evaluating on reload", async () => {
    const accepted = posting("accepted", "Persisted accepted role");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistJobList
        {...baseProps}
        aiFilterState={aiState({
          status: "processing",
          counts: { accepted: 1, rejected: 2, total: 3 },
        })}
        initialAiAcceptedPage={{
          postings: [accepted],
          nextOffset: 3,
          hasMore: true,
        }}
        aiFilterScopeKey="scope-1"
      />,
    );

    expect(screen.getByText("Persisted accepted role")).toBeTruthy();
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });
});
