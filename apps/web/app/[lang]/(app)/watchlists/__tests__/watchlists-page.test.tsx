import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";
import type { WatchlistPageData } from "@/lib/services/watchlist-page-data";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  createWatchlist: vi.fn(),
  createWatchlistFromHandoff: vi.fn(),
  selectOwnedWatchlist: vi.fn(),
  clearWatchlistSelection: vi.fn(),
  searchParams: new URLSearchParams(),
  session: { isLoggedIn: true, isPending: false },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mocks.push,
    replace: mocks.replace,
    refresh: mocks.refresh,
  }),
  useSearchParams: () => mocks.searchParams,
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/lib/actions/watchlists", () => ({
  createWatchlist: mocks.createWatchlist,
  createWatchlistFromHandoff: mocks.createWatchlistFromHandoff,
}));

vi.mock("@/lib/actions/watchlist-selection", () => ({
  selectOwnedWatchlist: mocks.selectOwnedWatchlist,
  clearWatchlistSelection: mocks.clearWatchlistSelection,
}));

vi.mock("../../[userSlug]/[watchlistSlug]/watchlist-view-page", () => ({
  WatchlistViewPage: ({ detail }: { detail: { id: string } }) => (
    <div data-testid="active-watchlist">{detail.id}</div>
  ),
}));

vi.mock("@/components/ui/Button", () => ({
  Button: ({ children, href }: { children: React.ReactNode; href?: string }) => (
    href ? <a href={href}>{children}</a> : <button type="button">{children}</button>
  ),
}));

import { WatchlistsPage } from "../watchlists-page";

const FIRST_ID = "11111111-1111-4111-8111-111111111111";
const SECOND_ID = "22222222-2222-4222-8222-222222222222";

function overview(id: string, title = id) {
  return {
    id,
    slug: title.toLowerCase(),
    title,
    description: null,
    isPublic: false,
    alertsEnabled: false,
    companyCount: 3,
    activeJobCount: null,
    lastAccessedAt: "2026-07-22T00:00:00.000Z",
    createdAt: "2026-07-22T00:00:00.000Z",
  };
}

function pageData(id: string): WatchlistPageData {
  return {
    detail: { id },
    postings: [],
    resolvedLocations: [],
    resolvedOccupations: [],
    resolvedSeniorities: [],
    resolvedTechnologies: [],
    jobLanguages: [],
    languages: [],
  } as unknown as WatchlistPageData;
}

const baseProps = {
  initialWatchlists: [overview(FIRST_ID, "Engineering")],
  initialPageData: pageData(FIRST_ID),
  selectionSync: "none" as const,
  limitReached: false,
  locale: "en",
};

describe("WatchlistsPage canonical private route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.searchParams = new URLSearchParams();
    mocks.session = { isLoggedIn: true, isPending: false };
    mocks.selectOwnedWatchlist.mockResolvedValue({ ok: true });
    mocks.clearWatchlistSelection.mockResolvedValue(undefined);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders the active watchlist and fills deferred counts", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ counts: { [FIRST_ID]: 42 } }),
    }));

    render(<WatchlistsPage {...baseProps} />);
    expect(screen.getByTestId("active-watchlist").textContent).toBe(FIRST_ID);
    await screen.findByText("42 jobs");
  });

  it("selects a card through the owner-validating action and refreshes in place", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(
      <WatchlistsPage
        {...baseProps}
        initialWatchlists={[
          overview(FIRST_ID, "Engineering"),
          overview(SECOND_ID, "Design"),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Design/ }));
    await waitFor(() => expect(mocks.selectOwnedWatchlist).toHaveBeenCalledWith(SECOND_ID));
    expect(mocks.refresh).toHaveBeenCalledOnce();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("synchronizes a server-chosen fallback without trusting client identity", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<WatchlistsPage {...baseProps} selectionSync="replace" />);

    await waitFor(() => expect(mocks.selectOwnedWatchlist).toHaveBeenCalledWith(FIRST_ID));
  });

  it("creates a handoff destination, selects it, and strips the mutating query", async () => {
    mocks.searchParams = new URLSearchParams({
      title: "Distributed systems",
      q: "platform,distributed",
      companies: "company-1",
    });
    mocks.createWatchlistFromHandoff.mockResolvedValue({
      id: SECOND_ID,
      slug: "distributed-systems",
    });

    render(
      <WatchlistsPage
        initialWatchlists={[]}
        initialPageData={null}
        selectionSync="none"
        limitReached={false}
        locale="en"
      />,
    );

    await waitFor(() => expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledOnce());
    expect(mocks.selectOwnedWatchlist).toHaveBeenCalledWith(SECOND_ID);
    expect(mocks.replace).toHaveBeenCalledWith("/en/watchlists");
  });

  it("renders the zero state and enforces the exact ten-watchlist ceiling", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    const ten = Array.from({ length: 10 }, (_, index) =>
      overview(
        `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
        `List ${index + 1}`,
      ),
    );
    const { rerender } = render(
      <WatchlistsPage
        initialWatchlists={[]}
        initialPageData={null}
        selectionSync="none"
        limitReached={false}
        locale="en"
      />,
    );
    expect(screen.getByText(/No watchlists yet/)).toBeTruthy();

    rerender(
      <WatchlistsPage
        initialWatchlists={ten}
        initialPageData={pageData(ten[0].id)}
        selectionSync="none"
        limitReached
        locale="en"
      />,
    );
    const create = screen.getByRole("button", { name: "Maximum of 10 watchlists reached" });
    expect(create.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(create);
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });
});
