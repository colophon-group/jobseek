import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  createWatchlist: vi.fn(),
  searchParams: new URLSearchParams(),
  session: {
    user: { username: "alice" } as { username: string } | null,
    isLoggedIn: true,
    isPending: false,
  },
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
}));

vi.mock("@/components/watchlist/watchlist-card", () => ({
  WatchlistCard: ({ watchlist }: { watchlist: { id: string; companyCount: number; activeJobCount: number | null } }) => (
    <div data-testid={`watchlist-${watchlist.id}`}>
      {watchlist.activeJobCount == null
        ? `${watchlist.companyCount} companies`
        : `${watchlist.activeJobCount} jobs`}
    </div>
  ),
  CreateWatchlistCard: () => <button type="button">Create</button>,
}));

vi.mock("@/components/watchlist/public-watchlist-search", () => ({
  PublicWatchlistSearch: () => null,
}));

vi.mock("@/components/ui/upgrade-modal", () => ({
  UpgradeModal: () => null,
  useUpgradeModal: () => ({
    open: false,
    setOpen: vi.fn(),
    reason: "",
    show: vi.fn(),
  }),
}));

vi.mock("@/components/ui/Button", () => ({
  Button: ({ children }: { children: React.ReactNode }) => <button type="button">{children}</button>,
}));

import { WatchlistsPage } from "../watchlists-page";

const overview = [{
  id: "watchlist-1",
  slug: "engineering",
  title: "Engineering",
  description: null,
  isPublic: false,
  alertsEnabled: false,
  companyCount: 3,
  activeJobCount: null,
  lastAccessedAt: "2026-07-22T00:00:00.000Z",
  createdAt: "2026-07-22T00:00:00.000Z",
}];

describe("WatchlistsPage deferred counts (#5896)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.searchParams = new URLSearchParams();
    mocks.session = {
      user: { username: "alice" },
      isLoggedIn: true,
      isPending: false,
    };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders useful cards before the count request settles", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    render(
      <WatchlistsPage
        initialWatchlists={overview}
        username="alice"
        limitReached={false}
        locale="en"
      />,
    );

    expect(screen.getByTestId("watchlist-watchlist-1").textContent).toBe(
      "3 companies",
    );
  });

  it("replaces the fallback with the live active-job count", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ counts: { "watchlist-1": 42 } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WatchlistsPage
        initialWatchlists={overview}
        username="alice"
        limitReached={false}
        locale="de"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("watchlist-watchlist-1").textContent).toBe(
        "42 jobs",
      );
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/watchlists/counts?locale=de",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("waits for bootstrap and creates one complete URL handoff", async () => {
    mocks.session = { user: null, isLoggedIn: false, isPending: true };
    mocks.searchParams = new URLSearchParams({
      title: "Distributed systems",
      description: "Senior platform roles",
      q: "platform,distributed",
      loc: "berlin,zurich",
      occ: "software-engineering",
      sen: "senior,staff",
      tech: "go,kubernetes",
      wm: "remote,hybrid",
      etype: "full_time,contract",
      sal: "120000-180000",
      salcur: "CHF",
      exp: "5-10",
      companies: "company-1,company-2",
    });
    mocks.createWatchlist.mockResolvedValue({ slug: "distributed-systems" });

    const props = {
      initialWatchlists: [],
      username: "alice",
      limitReached: false,
      locale: "en",
    };
    const { rerender } = render(<WatchlistsPage {...props} />);

    expect(mocks.createWatchlist).not.toHaveBeenCalled();

    mocks.session = {
      user: { username: "alice" },
      isLoggedIn: true,
      isPending: false,
    };
    rerender(<WatchlistsPage {...props} />);

    await waitFor(() => expect(mocks.createWatchlist).toHaveBeenCalledTimes(1));
    expect(mocks.createWatchlist).toHaveBeenCalledWith({
      title: "Distributed systems",
      description: "Senior platform roles",
      companyIds: ["company-1", "company-2"],
      filters: {
        keywords: ["platform", "distributed"],
        locationSlugs: ["berlin", "zurich"],
        occupationSlugs: ["software-engineering"],
        senioritySlugs: ["senior", "staff"],
        technologySlugs: ["go", "kubernetes"],
        workMode: ["remote", "hybrid"],
        employmentType: ["full_time", "contract"],
        salaryMin: 120000,
        salaryMax: 180000,
        salaryCurrency: "CHF",
        experienceMin: 5,
        experienceMax: 10,
      },
      isPublic: false,
    });
    expect(mocks.replace).toHaveBeenCalledWith(
      "/en/alice/distributed-systems",
    );
    expect(mocks.push).not.toHaveBeenCalled();

    rerender(<WatchlistsPage {...props} locale="de" />);
    await waitFor(() => expect(mocks.createWatchlist).toHaveBeenCalledTimes(1));
  });
});
