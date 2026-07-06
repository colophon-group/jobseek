/**
 * Regression tests for issue #3345 — watchlist job cards jump up and
 * down on scroll.
 *
 * The user-reported jitter was tracked to the browser's automatic scroll
 * anchoring picking arbitrary row elements as anchors during pagination,
 * plus the risk of content-driven height variation in date dividers and
 * postings rows. The fix:
 *
 *   1. The postings list container opts the whole subtree out of scroll
 *      anchoring with `[overflow-anchor:none]` — no element in the list
 *      can be picked by the anchoring heuristic mid-scroll.
 *   2. Each posting row carries an explicit `min-h-10` (= 40px, the
 *      current natural height) and `[contain:layout]` so per-row
 *      reflows can't propagate.
 *   3. Each date divider carries `min-h-7` (= 28px, the divider's
 *      current natural height with `py-2 + ~12px text`).
 *
 * These tests assert the class hooks are present so the fix can't
 * silently regress.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import "@/test-utils/lingui-mock";

// --- Mocks ------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/CompanyIcon", () => ({
  CompanyIcon: ({ alt }: { alt: string }) => <span data-testid="company-icon">{alt}</span>,
}));

vi.mock("@/lib/time", () => ({
  timeAgoShort: () => "1d",
}));

vi.mock("@/lib/search/search-runner", () => ({
  runGetWatchlistPostings: vi.fn().mockResolvedValue({
    postings: [],
    total: 0,
  }),
}));

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
  JobDetailPanel: () => <div data-testid="job-detail-panel" />,
}));

vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: () => ({ sentinelRef: { current: null }, isLoading: false }),
}));

vi.mock("@/lib/use-paginated-load-more", () => ({
  usePaginatedLoadMore: ({
    initialItems,
    initialTotal,
  }: {
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

vi.mock("@/components/TrackingDot", () => ({
  TrackingDot: () => <span data-testid="tracking-dot" />,
}));

vi.mock("@/components/PendingJobWarning", () => ({
  PendingJobIcon: () => <span data-testid="pending-job-icon" />,
}));

vi.mock("@/components/search/language-stats-row", () => ({
  LanguageStatsRow: () => null,
}));

vi.mock("@/components/watchlist/format-date-divider", () => ({
  formatDateDivider: () => "Today",
  getDateKey: (s: string) => s,
}));

beforeEach(() => {
  /* nothing */
});

// Import AFTER mocks so vi.mock hoisting is honored.
import { WatchlistJobList } from "../watchlist-job-list";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";

function entry(
  id: string,
  title: string | null = `Job ${id}`,
  firstSeenAt = "2026-05-13T00:00:00Z",
): WatchlistPostingEntry {
  return {
    id,
    title,
    sourceUrl: `https://example.com/${id}`,
    firstSeenAt,
    isActive: true,
    company: {
      id: `c-${id}`,
      name: `Company ${id}`,
      slug: `company-${id}`,
      icon: null,
    },
  };
}

function renderList(postings: WatchlistPostingEntry[]) {
  return render(
    <WatchlistJobList
      filters={{ companyIds: ["c-1"] }}
      initialPostings={postings}
      initialTotal={postings.length}
      yearTotal={postings.length}
      jobLanguages={["en"]}
      locale="en"
    />,
  );
}

describe("WatchlistJobList — scroll stability (issue #3345)", () => {
  it("the postings list container has `overflow-anchor: none`", () => {
    const { container } = renderList([entry("1"), entry("2")]);

    // The container must opt the whole list subtree out of the browser's
    // automatic scroll-anchor selection. Without this, the anchoring
    // heuristic can pick a row near the viewport edge during pagination
    // and adjust scroll position, producing the user-visible jitter.
    const anchored = container.querySelector("div.\\[overflow-anchor\\:none\\]");
    expect(
      anchored,
      "list container must carry `[overflow-anchor:none]` so the anchoring heuristic cannot pick a row mid-scroll",
    ).not.toBeNull();
  });

  it("each posting row reserves vertical space with `min-h-10`", () => {
    const { container } = renderList([entry("1"), entry("2")]);

    // Posting rows are the `<div>`s that contain the `Open job posting`
    // / `Company X — Job X` button (the absolute overlay).
    const rows = container.querySelectorAll("div.relative.flex.min-h-10");
    expect(rows.length).toBeGreaterThanOrEqual(2);
  });

  it("each posting row isolates its layout with `contain: layout`", () => {
    const { container } = renderList([entry("1"), entry("2")]);

    // `[contain:layout]` prevents a re-render of one row from triggering
    // a reflow of neighbouring rows. The Tailwind arbitrary-property
    // class compiles to `contain: layout`.
    const rows = container.querySelectorAll('div.\\[contain\\:layout\\]');
    expect(rows.length).toBeGreaterThanOrEqual(2);
  });

  it("the date divider reserves vertical space with `min-h-7`", () => {
    // Two postings from different days → one divider between them. The
    // first entry's date is the same as the second's → only one divider
    // at the top (today). To be safe we use two distinct dates.
    const { container } = renderList([
      entry("1", "Job 1", "2026-05-13T00:00:00Z"),
      entry("2", "Job 2", "2026-05-12T00:00:00Z"),
    ]);

    const dividers = container.querySelectorAll("div.flex.min-h-7");
    expect(
      dividers.length,
      "date dividers must carry `min-h-7` so a re-render cannot collapse their height",
    ).toBeGreaterThanOrEqual(1);
  });
});
