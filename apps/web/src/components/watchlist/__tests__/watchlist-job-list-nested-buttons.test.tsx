/**
 * Regression tests for issue #3166 — nested interactive elements in the
 * watchlist row.
 *
 * Before the fix, each row was a `<button>` with a nested `<span role=
 * "button">` for the save toggle. That violates WCAG 4.1.2 (nested
 * interactive widgets) and left the inner span unreachable by keyboard
 * (no `tabIndex={0}` and no `onKeyDown`).
 *
 * The fix restructures the row into a `relative` wrapper containing two
 * sibling `<button>` elements:
 *   - an "Open posting" overlay (`absolute inset-0`) that captures
 *     clicks anywhere on the row,
 *   - a real Save `<button>` (`relative z-10`) that overlays the row
 *     visually and intercepts its own clicks.
 *
 * These tests assert:
 *   1. The DOM no longer contains a `<button>` inside a `<button>`
 *      (the WCAG violation).
 *   2. Both buttons are real `<button>` elements, keyboard-reachable
 *      via natural Tab order.
 *   3. Clicking the save button does NOT trigger the open-posting
 *      handler (the original `e.stopPropagation()` invariant).
 *   4. Clicking elsewhere on the row DOES open the posting.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

const toggleMock = vi.fn();
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: true, isPending: false }),
}));
vi.mock("@/components/SavedJobsProvider", () => ({
  useSavedJobs: () => ({
    isSaved: () => false,
    toggle: toggleMock,
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

// Stub window.history APIs the row's open handler invokes.
beforeEach(() => {
  toggleMock.mockClear();
  // happy-dom provides history; nothing more needed.
});

// Import AFTER mocks so vi.mock hoisting is honored.
import { WatchlistJobList } from "../watchlist-job-list";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";

function entry(id: string, title: string | null = `Job ${id}`): WatchlistPostingEntry {
  return {
    id,
    title,
    sourceUrl: `https://example.com/${id}`,
    firstSeenAt: "2026-05-13T00:00:00Z",
    isActive: true,
    company: {
      id: `c-${id}`,
      name: `Company ${id}`,
      slug: `company-${id}`,
      icon: null,
    },
  };
}

function renderList(postings: WatchlistPostingEntry[], total = postings.length) {
  return render(
    <WatchlistJobList
      filters={{ companyIds: ["c-1"] }}
      initialPostings={postings}
      initialTotal={total}
      yearTotal={postings.length}
      jobLanguages={["en"]}
      locale="en"
    />,
  );
}

describe("WatchlistJobList row a11y (issue #3166)", () => {
  it("does NOT nest a <button> (or [role=button]) inside another <button>", () => {
    const { container } = renderList([entry("1"), entry("2")]);

    // Find every real <button>; assert none has a button-like descendant.
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const btn of buttons) {
      const nested = btn.querySelector('button, [role="button"]');
      expect(
        nested,
        `nested interactive element found inside <button>: ${btn.outerHTML.slice(0, 200)}`,
      ).toBeNull();
    }
  });

  it("renders two real <button>s per row — Open posting + Save job — both keyboard-reachable", () => {
    renderList([entry("1")]);

    // Both buttons must be real <button> elements (not [role=button] spans).
    const openBtn = screen.getByRole("button", { name: /Company 1 — Job 1|open job posting/i });
    const saveBtn = screen.getByRole("button", { name: /save job/i });
    expect(openBtn.tagName).toBe("BUTTON");
    expect(saveBtn.tagName).toBe("BUTTON");

    // Real <button>s are tab-focusable by default. The browser puts
    // tabIndex=0 implicitly; explicit tabIndex=-1 would opt them out.
    expect(openBtn.getAttribute("tabindex")).not.toBe("-1");
    expect(saveBtn.getAttribute("tabindex")).not.toBe("-1");
  });

  it("Tab order across two rows is: row1.open, row1.save, row2.open, row2.save", () => {
    const { container } = renderList([entry("1"), entry("2")]);

    // Take all <button>s in DOM order; they must be the four expected
    // ones in the expected sequence. Open buttons use the row title
    // (e.g. `Company 1 — Job 1`); save buttons use "Save job".
    const buttons = Array.from(container.querySelectorAll("button"));
    const labels = buttons.map((b) => b.getAttribute("aria-label") ?? "");

    // Classify each label as `open` or `save`. Anything else (e.g. date
    // dividers shouldn't add buttons, but be defensive) is ignored.
    const classified = labels
      .map((l) => {
        if (/save job/i.test(l)) return "save" as const;
        if (/^Company \d+ — Job \d+$/.test(l)) return "open" as const;
        if (/open job posting/i.test(l)) return "open" as const;
        return null;
      })
      .filter((x): x is "open" | "save" => x !== null);

    expect(classified).toEqual(["open", "save", "open", "save"]);
  });

  it("clicking the save button does NOT trigger the open-posting handler", () => {
    renderList([entry("1")]);

    const saveBtn = screen.getByRole("button", { name: /save job/i });
    fireEvent.click(saveBtn);

    // toggle was called exactly once with the entry id.
    expect(toggleMock).toHaveBeenCalledTimes(1);
    expect(toggleMock).toHaveBeenCalledWith("1");

    // The detail panel must NOT have appeared (open handler never fired).
    expect(screen.queryByTestId("job-detail-panel")).toBeNull();
  });

  it("clicking the row (open button) opens the posting; save handler not called", () => {
    renderList([entry("1")]);

    // Before click: the JobDetailPanel mock has not been rendered.
    expect(screen.queryAllByTestId("job-detail-panel")).toHaveLength(0);

    const openBtn = screen.getByRole("button", { name: /Company 1 — Job 1|open job posting/i });
    fireEvent.click(openBtn);

    // After click: the detail panel renders (there are two panes — a
    // sticky desktop one and a mobile drawer — both render under the
    // current breakpoint-agnostic layout).
    expect(
      screen.queryAllByTestId("job-detail-panel").length,
    ).toBeGreaterThan(0);
    expect(toggleMock).not.toHaveBeenCalled();
  });

  it("shows a refresh prompt when the watchlist count says jobs exist but the page is empty (#3403)", () => {
    renderList([], 3);

    expect(screen.getByRole("alert").textContent).toMatch(/oops, something went wrong/i);
    expect(screen.queryByText(/no jobs found/i)).toBeNull();
  });
});
