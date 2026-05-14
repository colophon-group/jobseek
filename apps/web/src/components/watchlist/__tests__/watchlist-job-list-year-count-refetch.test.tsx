/**
 * Regression test for issue #3344 — `yearTotal` ("in the last year")
 * went stale on filter change.
 *
 * Before this fix, the active count refetched on every filter change
 * (driven by `usePaginatedLoadMore`'s `resetKey`) but the year count
 * was rendered as a static prop from SSR. Editing a filter inline
 * updated the active badge but left the year badge unchanged until a
 * full page reload — visible divergence on the same row.
 *
 * The fix runs a parallel year-count refetch keyed on the same
 * `filtersKey` so both badges stay in lockstep.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const runGetWatchlistPostings = vi.fn().mockResolvedValue({
  postings: [],
  total: 0,
});
const runGetWatchlistPostingYearCount = vi.fn().mockResolvedValue(0);

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/search/search-runner", () => ({
  runGetWatchlistPostings: (...args: unknown[]) =>
    runGetWatchlistPostings(...args),
  runGetWatchlistPostingYearCount: (...args: unknown[]) =>
    runGetWatchlistPostingYearCount(...args),
}));

vi.mock("@/components/CompanyIcon", () => ({
  CompanyIcon: () => null,
}));
vi.mock("@/lib/time", () => ({ timeAgoShort: () => "" }));
vi.mock("@/lib/search/use-clear-typesense-on-auth-change", () => ({
  useClearTypesenseOnAuthChange: () => {},
}));
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: true, isPending: false }),
}));
vi.mock("@/components/SavedJobsProvider", () => ({
  useSavedJobs: () => ({
    isSaved: () => false,
    toggle: () => {},
    isToggling: () => false,
    getStatus: () => null,
    getSavedJobId: () => null,
    setStatus: () => {},
    onStatusChange: () => () => {},
  }),
}));
vi.mock("@/components/search/job-detail-dialog", () => ({
  JobDetailPanel: () => null,
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
    loadMore: () => {},
  }),
}));
vi.mock("@/components/InfiniteScrollSentinel", () => ({
  InfiniteScrollSentinel: () => null,
}));
vi.mock("@/components/TruncationPrompt", () => ({
  TruncationPrompt: () => null,
}));
vi.mock("@/components/TrackingDot", () => ({ TrackingDot: () => null }));
vi.mock("@/components/PendingJobWarning", () => ({
  PendingJobIcon: () => null,
}));

// Render the year-count value as a stable test surface.
vi.mock("@/components/search/language-stats-row", () => ({
  LanguageStatsRow: ({ yearCount }: { yearCount: number }) => (
    <div data-testid="year-count">{yearCount}</div>
  ),
}));

vi.mock("@/components/watchlist/format-date-divider", () => ({
  formatDateDivider: () => "",
  getDateKey: (s: string) => s,
}));

beforeEach(() => {
  runGetWatchlistPostings.mockClear();
  runGetWatchlistPostingYearCount.mockClear();
  runGetWatchlistPostingYearCount.mockResolvedValue(0);
});

import { WatchlistJobList } from "../watchlist-job-list";

const baseProps = {
  initialPostings: [],
  initialTotal: 0,
  jobLanguages: ["en"],
  locale: "en",
};

describe("WatchlistJobList — year-count refetch on filter change (#3344)", () => {
  it("does NOT refire the year-count fetch on initial mount (SSR yearTotal already covers it)", async () => {
    render(
      <WatchlistJobList
        filters={{ companyIds: ["c-1"] }}
        yearTotal={88343}
        {...baseProps}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(runGetWatchlistPostingYearCount).not.toHaveBeenCalled();
  });

  it("refires the year-count fetch when filters change, and updates the rendered badge", async () => {
    const { rerender, getByTestId } = render(
      <WatchlistJobList
        filters={{ companyIds: ["c-1"] }}
        yearTotal={88343}
        {...baseProps}
      />,
    );

    expect(getByTestId("year-count").textContent).toBe("88343");

    runGetWatchlistPostingYearCount.mockResolvedValueOnce(12345);
    rerender(
      <WatchlistJobList
        filters={{ companyIds: ["c-1"], keywords: ["engineer"] }}
        yearTotal={88343}
        {...baseProps}
      />,
    );

    // Drain the effect's microtask + the resolved fetch.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(runGetWatchlistPostingYearCount).toHaveBeenCalledTimes(1);
    expect(getByTestId("year-count").textContent).toBe("12345");
  });

  it("ignores a stale result if filters change again before the previous fetch resolves", async () => {
    let resolveFirst: ((n: number) => void) | undefined;
    runGetWatchlistPostingYearCount.mockImplementationOnce(
      () => new Promise<number>((res) => { resolveFirst = res; }),
    );
    runGetWatchlistPostingYearCount.mockResolvedValueOnce(7777);

    const { rerender, getByTestId } = render(
      <WatchlistJobList
        filters={{ companyIds: ["c-1"] }}
        yearTotal={1000}
        {...baseProps}
      />,
    );

    rerender(
      <WatchlistJobList
        filters={{ companyIds: ["c-1"], keywords: ["alpha"] }}
        yearTotal={1000}
        {...baseProps}
      />,
    );
    rerender(
      <WatchlistJobList
        filters={{ companyIds: ["c-1"], keywords: ["beta"] }}
        yearTotal={1000}
        {...baseProps}
      />,
    );

    // Resolve the first (now-stale) fetch.
    resolveFirst?.(1);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // The second (latest) fetch wins.
    expect(getByTestId("year-count").textContent).toBe("7777");
  });
});
