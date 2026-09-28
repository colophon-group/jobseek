import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/CompanyIcon", () => ({ CompanyIcon: () => null }));
vi.mock("@/lib/time", () => ({ timeAgoShort: () => "1h" }));
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: false, isPending: false }),
}));
vi.mock("@/components/providers/SavedJobsProvider", () => ({
  useSavedJobs: () => ({ isSaved: () => false, toggle: vi.fn() }),
}));
vi.mock("@/components/search/job-detail-dialog", () => ({
  JobDetailPanel: ({ postingId, onClose }: { postingId: string; onClose: () => void }) => (
    <section data-testid="job-detail" data-posting-id={postingId}>
      <h2>Details for {postingId}</h2>
      <button onClick={onClose}>Close job details</button>
    </section>
  ),
}));
// Keep the real mobile dialog: its Radix portal must disappear on view changes.
vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: () => ({ sentinelRef: vi.fn(), isLoading: false }),
}));
vi.mock("@/lib/use-paginated-load-more", () => ({
  usePaginatedLoadMore: ({ initialItems, initialTotal }: {
    initialItems: unknown[]; initialTotal: number;
  }) => ({
    items: initialItems, total: initialTotal, truncated: false,
    hasMore: false, loadMore: vi.fn(), resultRevision: 0,
  }),
}));
vi.mock("@/components/InfiniteScrollSentinel", () => ({ InfiniteScrollSentinel: () => null }));
vi.mock("@/components/TruncationPrompt", () => ({ TruncationPrompt: () => null }));
vi.mock("@/components/TrackingDot", () => ({ TrackingDot: () => null }));
vi.mock("@/components/PendingJobWarning", () => ({ PendingJobIcon: () => null }));
vi.mock("@/components/search/language-stats-row", () => ({ LanguageStatsRow: () => null }));
vi.mock("@/components/watchlist/format-date-divider", () => ({
  formatDateDivider: () => "Today", getDateKey: (value: string) => value,
}));
vi.mock("@/lib/search/search-runner", () => ({
  runGetWatchlistPostings: vi.fn(), runGetWatchlistPostingYearCount: vi.fn(),
}));

import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";
import type { AiFilterUiState } from "@/lib/ai-filter/ui-contract";
import { WatchlistJobList } from "../watchlist-job-list";

function posting(id: string): WatchlistPostingEntry {
  return {
    id, title: `Job ${id}`, sourceUrl: `https://example.com/${id}`,
    firstSeenAt: "2026-09-21T10:00:00.000Z", isActive: true,
    company: { id: "company-1", name: "Example", slug: "example", icon: null },
  };
}

const state: AiFilterUiState = {
  watchlistId: "11111111-1111-4111-8111-111111111111",
  enabled: true, entitled: true, query: "Work on robots",
  queryRevision: 1, queryVersionId: "22222222-2222-4222-8222-222222222222",
  status: "caught_up", counts: { accepted: 1, rejected: 1, total: 2 },
  progress: { selectionOffset: 2, scannedCount: 2, completedCount: 2, stopReason: null },
  lastCaughtUpAt: null, latestEventSequence: 1,
};
const common = {
  filters: { companyIds: ["company-1"] }, yearTotal: 2, jobLanguages: ["en"], locale: "en",
};

// Match the page composition: the broad feed remains mounted while its drawer
// contains another real WatchlistJobList. Neither list's selection is mocked.
function Results({ narrowed, onToggle }: { narrowed: boolean; onToggle?: () => void }) {
  return (
    <WatchlistJobList
      {...common} initialPostings={[posting("A")]} initialTotal={1}
      resultMode="broad" drawerOpen={narrowed}
      drawerControl={(
        <>
          <button onClick={onToggle}>{narrowed ? "All" : "Narrowed"}</button>
          <div hidden={!narrowed}>
            <WatchlistJobList
              {...common} initialPostings={[]} initialTotal={0} resultMode="narrowed"
              aiFilterState={state} aiFilterScopeReady={narrowed} aiFilterReadOnly
              initialAiAcceptedPage={{
                queryVersionId: state.queryVersionId!, postings: [posting("B")],
                total: 1, nextOffset: 2, hasMore: false,
              }}
            />
          </div>
        </>
      )}
    />
  );
}
function ToggleResults({ initiallyNarrowed = false }: { initiallyNarrowed?: boolean }) {
  const [narrowed, setNarrowed] = useState(initiallyNarrowed);
  return <Results narrowed={narrowed} onToggle={() => setNarrowed(!narrowed)} />;
}
function openJob(id: string) {
  fireEvent.click(screen.getByRole("button", { name: `Example — Job ${id}` }));
}
function expectOnlyDetail(id: string) {
  expect(screen.getAllByTestId("job-detail").map((node) => node.dataset.postingId)).toEqual([id]);
  expect(new URLSearchParams(window.location.search).get("show")).toBe(id);
}
function expectNoDetails() {
  expect(screen.queryAllByTestId("job-detail")).toHaveLength(0);
  expect(new URLSearchParams(window.location.search).has("show")).toBe(false);
}
function setMobile(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches, media: "(max-width: 1023px)", onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
}

beforeEach(() => {
  window.history.replaceState({ preserved: true }, "", "/en/watchlists/robotics?sort=recent#jobs");
  setMobile(false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("watchlist detail selection across result views", () => {
  it("replaces All details with Narrowed details and never resurrects either selection", () => {
    render(<ToggleResults />);
    openJob("A");
    expectOnlyDetail("A");
    fireEvent.click(screen.getByRole("button", { name: "Narrowed" }));
    expectNoDetails();
    openJob("B");
    expectOnlyDetail("B");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expectNoDetails();
    openJob("A");
    expectOnlyDetail("A");
    fireEvent.click(screen.getByRole("button", { name: "Close job details" }));
    expectNoDetails();
    fireEvent.click(screen.getByRole("button", { name: "Narrowed" }));
    expectNoDetails();
    expect(window.location.search).toBe("?sort=recent");
    expect(window.location.hash).toBe("#jobs");
    expect(window.history.state).toEqual({ preserved: true });
  });

  it("clears a Narrowed selection when returning to All before opening another job", () => {
    render(<ToggleResults initiallyNarrowed />);
    openJob("B");
    expectOnlyDetail("B");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expectNoDetails();
    openJob("A");
    expectOnlyDetail("A");
    fireEvent.click(screen.getByRole("button", { name: "Narrowed" }));
    expectNoDetails();
  });

  it.each([false, true])("initializes a deep link only once (Narrowed=%s)", (narrowed) => {
    window.history.replaceState(null, "", "/en/watchlists/robotics?show=B");
    render(<ToggleResults initiallyNarrowed={narrowed} />);
    expectOnlyDetail("B");
    fireEvent.click(screen.getByRole("button", { name: "Close job details" }));
    expectNoDetails();
    fireEvent.click(screen.getByRole("button", { name: narrowed ? "All" : "Narrowed" }));
    expectNoDetails();
  });

  it("removes the real mobile portal on a view change, close, and unmount", () => {
    setMobile(true);
    const view = render(<Results narrowed={false} />);
    openJob("A");
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByTestId("job-detail").dataset.postingId).toBe("A");
    expect(view.container.contains(dialog)).toBe(false);
    view.rerender(<Results narrowed />);
    expect(screen.queryAllByRole("dialog")).toHaveLength(0);
    expectNoDetails();
    openJob("B");
    dialog = screen.getByRole("dialog");
    expect(within(dialog).getAllByRole("button", { name: "Close job details" })).toHaveLength(1);
    expect(within(dialog).getByTestId("job-detail").dataset.postingId).toBe("B");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close job details" }));
    expect(screen.queryAllByRole("dialog")).toHaveLength(0);
    expectNoDetails();
    view.rerender(<Results narrowed={false} />);
    expectNoDetails();
    openJob("A");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    view.unmount();
    expect(screen.queryAllByRole("dialog")).toHaveLength(0);
    expect(screen.queryAllByTestId("job-detail")).toHaveLength(0);
  });
});
