import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  createWatchlist: vi.fn(),
  createWatchlistFromHandoff: vi.fn(),
  showLimit: vi.fn(),
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
  createWatchlistFromHandoff: mocks.createWatchlistFromHandoff,
}));

vi.mock("@/components/watchlist/watchlist-card", () => ({
  WatchlistCard: ({ watchlist }: { watchlist: { id: string; companyCount: number; activeJobCount: number | null } }) => (
    <div data-testid={`watchlist-${watchlist.id}`}>
      {watchlist.activeJobCount == null
        ? `${watchlist.companyCount} companies`
        : `${watchlist.activeJobCount} jobs`}
    </div>
  ),
  CreateWatchlistCard: ({
    onClick,
    onLimitReached,
    limitReached,
  }: {
    onClick: () => void;
    onLimitReached: () => void;
    limitReached?: boolean;
  }) => (
    <button
      type="button"
      data-limit-reached={String(Boolean(limitReached))}
      onClick={limitReached ? onLimitReached : onClick}
    >
      Create
    </button>
  ),
}));

vi.mock("@/components/watchlist/public-watchlist-search", () => ({
  PublicWatchlistSearch: () => null,
}));

vi.mock("@/components/watchlist/watchlist-limit-modal", () => ({
  WatchlistLimitModal: () => null,
  useWatchlistLimitModal: () => ({
    open: false,
    setOpen: vi.fn(),
    show: mocks.showLimit,
  }),
}));

vi.mock("@/components/ui/Button", () => ({
  Button: ({ children, href }: { children: React.ReactNode; href?: string }) => (
    href ? <a href={href}>{children}</a> : <button type="button">{children}</button>
  ),
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
    mocks.createWatchlist.mockReset();
    mocks.createWatchlistFromHandoff.mockReset();
    mocks.showLimit.mockReset();
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

  it("passes the initial cap state to Create and shows the neutral notice", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ counts: {} }),
    }));

    render(
      <WatchlistsPage
        initialWatchlists={overview}
        username="alice"
        limitReached
        locale="en"
      />,
    );

    const create = screen.getByRole("button", { name: "Create" });
    expect(create.getAttribute("data-limit-reached")).toBe("true");
    fireEvent.click(create);
    expect(mocks.showLimit).toHaveBeenCalledOnce();
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });

  it("updates the overview cap state after an authoritative create rejection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ counts: {} }),
    }));
    mocks.createWatchlist.mockResolvedValue({ error: "limit_reached" });

    render(
      <WatchlistsPage
        initialWatchlists={overview}
        username="alice"
        limitReached={false}
        locale="en"
      />,
    );

    const create = screen.getByRole("button", { name: "Create" });
    expect(create.getAttribute("data-limit-reached")).toBe("false");
    fireEvent.click(create);

    await waitFor(() => {
      expect(mocks.showLimit).toHaveBeenCalledOnce();
      expect(create.getAttribute("data-limit-reached")).toBe("true");
    });
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
    mocks.createWatchlistFromHandoff.mockResolvedValue({
      slug: "distributed-systems",
    });

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

    await waitFor(() => (
      expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledTimes(1)
    ));
    expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledWith({
      title: "Distributed systems",
      description: "Senior platform roles",
      companySlugs: ["company-1", "company-2"],
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
    });
    expect(mocks.replace).toHaveBeenCalledWith(
      "/en/alice/distributed-systems",
    );
    expect(mocks.push).not.toHaveBeenCalled();

    rerender(<WatchlistsPage {...props} locale="de" />);
    await waitFor(() => (
      expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledTimes(1)
    ));
  });

  it("recovers truthfully when a URL handoff loses the final slot", async () => {
    mocks.searchParams = new URLSearchParams({
      title: "Retryable roles",
      companies: "stripe",
    });
    mocks.createWatchlistFromHandoff.mockResolvedValue({
      error: "limit_reached",
    });

    const props = {
      initialWatchlists: overview,
      username: "alice",
      limitReached: false,
      locale: "en",
    };
    const { rerender } = render(<WatchlistsPage {...props} />);

    await waitFor(() => {
      expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledTimes(1);
      expect(mocks.showLimit).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("button", { name: "Create" }).getAttribute(
        "data-limit-reached",
      )).toBe("true");
    });
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.searchParams.toString()).toBe(
      "title=Retryable+roles&companies=stripe",
    );

    rerender(<WatchlistsPage {...props} locale="de" />);
    await waitFor(() => {
      expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledTimes(1);
      expect(mocks.showLimit).toHaveBeenCalledTimes(1);
    });
  });

  it("preserves the handoff across an anonymous sign-in return", async () => {
    mocks.searchParams = new URLSearchParams({
      title: "Stripe roles",
      companies: "stripe",
    });
    mocks.session = { user: null, isLoggedIn: false, isPending: false };

    const props = {
      initialWatchlists: [],
      username: null,
      limitReached: false,
      locale: "en",
    };
    const anonymous = render(<WatchlistsPage {...props} />);
    const signIn = screen.getByRole("link", { name: "Log in" });
    const signInUrl = new URL(signIn.getAttribute("href")!, "https://jseek.co");
    expect(signInUrl.pathname).toBe("/en/sign-in");
    expect(signInUrl.searchParams.get("next")).toBe(
      "/en/watchlists?title=Stripe+roles&companies=stripe",
    );
    expect(mocks.createWatchlistFromHandoff).not.toHaveBeenCalled();

    anonymous.unmount();
    mocks.session = { user: null, isLoggedIn: false, isPending: true };
    mocks.createWatchlistFromHandoff.mockResolvedValue({ slug: "stripe-roles" });
    const authenticated = render(<WatchlistsPage {...props} />);
    mocks.session = {
      user: { username: "alice" },
      isLoggedIn: true,
      isPending: false,
    };
    authenticated.rerender(<WatchlistsPage {...props} username="alice" />);

    await waitFor(() => (
      expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledTimes(1)
    ));
    expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledWith(
      expect.objectContaining({ companySlugs: ["stripe"] }),
    );
  });

  it("handles a terminal handoff failure and retains the retry URL", async () => {
    mocks.searchParams = new URLSearchParams({
      title: "Retryable roles",
      companies: "stripe",
    });
    mocks.createWatchlistFromHandoff.mockRejectedValue(
      new Error("database unavailable"),
    );

    render(
      <WatchlistsPage
        initialWatchlists={[]}
        username="alice"
        limitReached={false}
        locale="en"
      />,
    );

    await waitFor(() => (
      expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledTimes(1)
    ));
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.searchParams.toString()).toBe(
      "title=Retryable+roles&companies=stripe",
    );
  });
});
