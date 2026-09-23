import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  createWatchlist: vi.fn(),
  createWatchlistFromHandoff: vi.fn(),
  copySharedWatchlist: vi.fn(),
  shareWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  getSessionWatchlistActivityPreviews: vi.fn(),
  searchParams: new URLSearchParams(),
  session: { isLoggedIn: true, isPending: false },
  rates: [] as { currency: string; toEur: number }[],
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

vi.mock("@/components/providers/SalaryDisplayProvider", () => ({
  useSalaryRates: () => mocks.rates,
}));

vi.mock("@/lib/actions/watchlists", () => ({
  createWatchlist: mocks.createWatchlist,
  createWatchlistFromHandoff: mocks.createWatchlistFromHandoff,
  copySharedWatchlist: mocks.copySharedWatchlist,
  shareWatchlist: mocks.shareWatchlist,
  deleteWatchlist: mocks.deleteWatchlist,
}));

vi.mock("@/lib/actions/session-watchlists", () => ({
  getSessionWatchlistActivityPreviews: mocks.getSessionWatchlistActivityPreviews,
}));

vi.mock("@/components/ui/Button", () => ({
  Button: ({ children, href, onClick }: { children: React.ReactNode; href?: string; onClick?: () => void }) => (
    href ? <a href={href}>{children}</a> : <button type="button" onClick={onClick}>{children}</button>
  ),
}));

import { WatchlistsPage } from "../watchlists-page";
import { stagePendingWatchlist } from "@/lib/pending-watchlist";

const FIRST_ID = "11111111-1111-4111-8111-111111111111";
const SECOND_ID = "22222222-2222-4222-8222-222222222222";
const THIRD_ID = "33333333-3333-4333-8333-333333333333";

function overview(id: string, title = id) {
  return {
    id,
    slug: title.toLowerCase(),
    title,
    description: null,
    isShared: false,
    alertsEnabled: false,
    companyCount: 0,
    activeJobCount: null,
    lastAccessedAt: "2026-07-22T00:00:00.000Z",
    createdAt: "2026-07-22T00:00:00.000Z",
  };
}

const baseProps = {
  initialWatchlists: [overview(FIRST_ID, "Engineering")],
  limitReached: false,
  locale: "en",
};

describe("WatchlistsPage private overview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.searchParams = new URLSearchParams();
    mocks.session = { isLoggedIn: true, isPending: false };
    mocks.rates = [];
    window.sessionStorage.clear();
    mocks.shareWatchlist.mockResolvedValue({
      ok: true,
      url: `https://jseek.co/watchlists/${FIRST_ID}`,
    });
    mocks.deleteWatchlist.mockResolvedValue({ ok: true });
    mocks.copySharedWatchlist.mockResolvedValue({
      id: SECOND_ID,
      slug: "cloned",
    });
    mocks.getSessionWatchlistActivityPreviews.mockResolvedValue({ previews: {} });
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders only overview links and never embeds a selected watchlist detail", () => {
    render(
      <WatchlistsPage
        {...baseProps}
        initialWatchlists={[
          overview(FIRST_ID, "Engineering"),
          overview(SECOND_ID, "Design"),
        ]}
      />,
    );

    expect(screen.getByRole("link", { name: /Engineering/ }).getAttribute("href"))
      .toBe(`/en/watchlists/${FIRST_ID}`);
    expect(screen.getByRole("link", { name: /Design/ }).getAttribute("href"))
      .toBe(`/en/watchlists/${SECOND_ID}`);
    expect(screen.queryByTestId("active-watchlist")).toBeNull();
  });

  it("shows a neutral loading state while client authentication hydrates", () => {
    mocks.session = { isLoggedIn: false, isPending: true };

    render(<WatchlistsPage {...baseProps} />);

    expect(screen.getByRole("status").textContent).toContain("Loading watchlists");
    expect(screen.queryByText(/Sign in to create and manage/)).toBeNull();
    expect(screen.queryByRole("link", { name: /Log in/ })).toBeNull();
  });

  it("adds an anonymous watchlist as a normal overview row without opening login", () => {
    mocks.session = { isLoggedIn: false, isPending: false };
    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(mocks.push).toHaveBeenCalledWith(
      expect.stringMatching(/^\/en\/watchlists\/[0-9a-f-]+$/),
    );
    expect(window.sessionStorage.length).toBe(1);
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
    expect(screen.queryByText("Session draft")).toBeNull();
    expect(screen.getByText("New watchlist")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain(
      "Saved in this browser until you log in",
    );
    expect(screen.queryByRole("button", { name: "Log in to keep" })).toBeNull();
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows.at(-1)?.contains(screen.getByRole("button", { name: "Create" })))
      .toBe(true);
  });

  it("keeps multiple anonymous watchlists and the Create row in the same list", () => {
    mocks.session = { isLoggedIn: false, isPending: false };
    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(screen.getAllByText("New watchlist")).toHaveLength(2);
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("restores an anonymous watchlist with the normal activity preview", async () => {
    stagePendingWatchlist({
      kind: "create",
      draft: {
        title: "Swiss internships",
        companyIds: [],
        filters: { anyCompany: true, locationSlugs: ["switzerland"] },
        isPublic: false,
      },
    });
    mocks.getSessionWatchlistActivityPreviews.mockImplementation(async ({ entries }) => ({
      previews: {
        [entries[0].id]: {
          activeCompanyCount: 3,
          activeJobCount: 24,
          topCompanies: [
            { id: "company-1", name: "Acme", icon: null },
          ],
        },
      },
    }));
    mocks.session = { isLoggedIn: false, isPending: false };

    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    expect(screen.getByText("Swiss internships")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("3 companies")).toBeTruthy());
    expect(screen.getByText("24 jobs")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Acme" })).toBeTruthy();
    expect(mocks.getSessionWatchlistActivityPreviews).toHaveBeenCalledWith({
      entries: expect.arrayContaining([
        expect.objectContaining({
          intent: expect.objectContaining({ kind: "create" }),
        }),
      ]),
      locale: "en",
    });
    const shareButtons = screen.getAllByRole("button", { name: "Log in to share" });
    expect(shareButtons.length).toBeGreaterThan(0);
    expect(shareButtons.every((button) => button.getAttribute("aria-disabled") === "true"))
      .toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(window.sessionStorage.length).toBe(0);
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
  });

  it("creates a staged anonymous draft after authentication", async () => {
    stagePendingWatchlist({
      kind: "create",
      draft: {
        title: "Swiss internships",
        companyIds: [],
        filters: { anyCompany: true, locationSlugs: ["switzerland"] },
        isPublic: false,
      },
    });
    mocks.createWatchlist.mockResolvedValue({
      id: SECOND_ID,
      slug: "swiss-internships",
    });

    render(<WatchlistsPage {...baseProps} />);

    await waitFor(() => expect(mocks.createWatchlist).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Swiss internships" }),
    ));
    expect(mocks.replace).toHaveBeenCalledWith(`/en/watchlists/${SECOND_ID}`);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("imports every staged anonymous watchlist after authentication", async () => {
    stagePendingWatchlist({
      kind: "create",
      draft: {
        title: "Swiss internships",
        companyIds: [],
        filters: { anyCompany: true, locationSlugs: ["switzerland"] },
        isPublic: false,
      },
    });
    stagePendingWatchlist({
      kind: "create",
      draft: {
        title: "Remote roles",
        companyIds: [],
        filters: { anyCompany: true, workMode: ["remote"] },
        isPublic: false,
      },
    });
    mocks.createWatchlist
      .mockResolvedValueOnce({ id: SECOND_ID, slug: "swiss-internships" })
      .mockResolvedValueOnce({ id: THIRD_ID, slug: "remote-roles" });

    render(<WatchlistsPage {...baseProps} />);

    await waitFor(() => expect(mocks.createWatchlist).toHaveBeenCalledTimes(2));
    expect(mocks.refresh).toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("clones a staged shared watchlist after authentication", async () => {
    stagePendingWatchlist({ kind: "clone", watchlistId: FIRST_ID });

    render(<WatchlistsPage {...baseProps} />);

    await waitFor(() => expect(mocks.copySharedWatchlist).toHaveBeenCalledWith(FIRST_ID));
    expect(mocks.replace).toHaveBeenCalledWith(`/en/watchlists/${SECOND_ID}`);
  });

  it("discards a staged watchlist when the authenticated account is full", async () => {
    stagePendingWatchlist({
      kind: "create",
      draft: {
        title: "Overflow",
        companyIds: [],
        filters: { anyCompany: true },
        isPublic: false,
      },
    });

    render(<WatchlistsPage {...baseProps} limitReached />);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Maximum of 10 watchlists reached. Extra saved watchlists were discarded.",
    );
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("fills cards from activity previews rather than static scope metadata", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        counts: { [FIRST_ID]: 42 },
        previews: {
          [FIRST_ID]: {
            activeJobCount: 42,
            activeCompanyCount: 2,
            topCompanies: [
              { id: "company-1", name: "Acme", icon: null },
              { id: "company-2", name: "Beta", icon: null },
            ],
          },
        },
      }),
    }));

    render(<WatchlistsPage {...baseProps} />);

    await screen.findByText("2 companies");
    expect(screen.getByText("42 jobs")).toBeTruthy();
    expect(screen.getByLabelText("Acme, Beta")).toBeTruthy();
    expect(screen.queryByText("All companies")).toBeNull();
    expect(fetch).toHaveBeenCalledWith(
      "/api/web/watchlists/counts?locale=en",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(screen.getByRole("status").textContent).toBe(
      "Watchlist activity finished loading.",
    );
    expect(screen.getByRole("status").parentElement?.getAttribute("aria-busy"))
      .toBe("false");
  });

  it("keeps Create as the final row inside the viewport-aware vertical scroller", () => {
    const rows = Array.from({ length: 8 }, (_, index) =>
      overview(
        `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
        `List ${index + 1}`,
      ),
    );
    render(<WatchlistsPage {...baseProps} initialWatchlists={rows} />);

    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    const create = screen.getByRole("button", { name: /Create/ });
    expect(list.className).toContain("pb-10");
    expect(list.className).toContain("md:pb-6");
    expect(items).toHaveLength(9);
    expect(items.at(-1)?.contains(create)).toBe(true);
    expect(screen.getByRole("link", { name: /List 8/ })).toBeTruthy();
    const scrollSurface = list.parentElement;
    const fadeWrapper = scrollSurface?.parentElement;
    expect(scrollSurface?.className).toContain("overflow-y-auto");
    expect(scrollSurface?.className).not.toContain("overflow-x-auto");
    expect(scrollSurface?.className).toContain("scrollbar-hide");
    expect(fadeWrapper?.className).toContain("max-h-[max(12rem,calc(100dvh_-_8rem))]");
    expect(fadeWrapper?.className).toContain("overflow-hidden");
    expect(create.className).toContain("w-full");
    expect(fadeWrapper?.parentElement?.className).toContain("mx-auto");
    expect(fadeWrapper?.parentElement?.className).toContain("w-full");
    expect(fadeWrapper?.parentElement?.className).toContain("max-w-3xl");
  });

  it("keeps Create as the only scroll row in the zero state", () => {
    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    expect(screen.getByText(/No watchlists yet/)).toBeTruthy();
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0].contains(screen.getByRole("button", { name: /Create/ })))
      .toBe(true);
  });

  it("settles missing activity previews instead of leaving permanent loading skeletons", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ counts: {}, previews: {} }),
    }));

    render(<WatchlistsPage {...baseProps} />);

    expect(await screen.findByText("— companies")).toBeTruthy();
    expect(screen.getByText("— jobs")).toBeTruthy();
    expect(document.querySelector(".animate-pulse")).toBeNull();
  });

  it("enables sharing before copying the opaque URL and falls back when Clipboard rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("permission denied"));
    const execCommand = vi.fn().mockReturnValue(true);
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const execDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    try {
      render(<WatchlistsPage {...baseProps} />);
      const desktopShare = screen.getAllByRole("button", { name: "Share" })
        .find((button) => button.className.includes("size-8"))!;
      fireEvent.click(desktopShare);

      expect((await screen.findByRole("status")).textContent).toBe("Link copied");
      expect(mocks.shareWatchlist).toHaveBeenCalledWith(FIRST_ID);
      expect(writeText).toHaveBeenCalledWith(
        `https://jseek.co/watchlists/${FIRST_ID}`,
      );
      expect(execCommand).toHaveBeenCalledWith("copy");
      expect(mocks.shareWatchlist.mock.invocationCallOrder[0]).toBeLessThan(
        writeText.mock.invocationCallOrder[0],
      );
    } finally {
      if (clipboardDescriptor) {
        Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
      } else {
        Reflect.deleteProperty(navigator, "clipboard");
      }
      if (execDescriptor) {
        Object.defineProperty(document, "execCommand", execDescriptor);
      } else {
        Reflect.deleteProperty(document, "execCommand");
      }
    }
  });

  it("deletes from the card, refreshes the overview, and returns focus to Create", async () => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    render(<WatchlistsPage {...baseProps} />);

    const desktopDelete = screen.getAllByRole("button", { name: "Delete" })
      .find((button) => button.className.includes("size-8"))!;
    fireEvent.click(desktopDelete);
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(mocks.deleteWatchlist).toHaveBeenCalledWith(FIRST_ID));
    expect(mocks.refresh).toHaveBeenCalledOnce();
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("button", { name: /Create/ }));
    });
  });

  it("creates a watchlist and pushes its direct owner-only detail route", async () => {
    mocks.createWatchlist.mockResolvedValue({
      id: SECOND_ID,
      slug: "new-watchlist",
    });
    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    fireEvent.click(screen.getByRole("button", { name: /Create/ }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(
      `/en/watchlists/${SECOND_ID}`,
    ));
  });

  it("surfaces a create failure and restores the Create control", async () => {
    mocks.createWatchlist.mockRejectedValueOnce(new Error("database unavailable"));
    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    fireEvent.click(screen.getByRole("button", { name: /Create/ }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not create this watchlist.",
    );
    expect(screen.getByRole("button", { name: /Create/ }).hasAttribute("disabled"))
      .toBe(false);
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("creates a handoff destination and replaces with its direct detail route", async () => {
    mocks.searchParams = new URLSearchParams({
      title: "Distributed systems",
      q: "platform,distributed",
      companies: "company-1",
    });
    mocks.createWatchlistFromHandoff.mockResolvedValue({
      id: SECOND_ID,
      slug: "distributed-systems",
    });

    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    await waitFor(() => expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledOnce());
    expect(mocks.replace).toHaveBeenCalledWith(`/en/watchlists/${SECOND_ID}`);
  });

  it("converts the EUR handoff range into the requested stored display currency", async () => {
    mocks.rates = [{ currency: "USD", toEur: 0.92 }];
    mocks.searchParams = new URLSearchParams({
      title: "US platform roles",
      sal: "92000-",
      salcur: "USD",
    });
    mocks.createWatchlistFromHandoff.mockResolvedValue({
      id: SECOND_ID,
      slug: "us-platform-roles",
    });

    render(<WatchlistsPage {...baseProps} initialWatchlists={[]} />);

    await waitFor(() => expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledWith(
      expect.objectContaining({
        filters: expect.objectContaining({
          salaryCurrency: "USD",
          salaryMin: 100_000,
        }),
      }),
    ));
  });

  it("does not loop, navigate, or change selection after a failed handoff", async () => {
    mocks.searchParams = new URLSearchParams({
      title: "Retryable roles",
      companies: "stripe",
    });
    mocks.createWatchlistFromHandoff.mockRejectedValue(
      new Error("database unavailable"),
    );

    const { rerender } = render(<WatchlistsPage {...baseProps} />);
    await waitFor(() => expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledOnce());
    rerender(<WatchlistsPage {...baseProps} locale="de" />);

    expect(mocks.createWatchlistFromHandoff).toHaveBeenCalledOnce();
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.searchParams.toString()).toBe("title=Retryable+roles&companies=stripe");
  });

  it("enforces the exact ten-watchlist ceiling", () => {
    const ten = Array.from({ length: 10 }, (_, index) =>
      overview(
        `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
        `List ${index + 1}`,
      ),
    );
    render(
      <WatchlistsPage
        initialWatchlists={ten}
        limitReached
        locale="en"
      />,
    );

    const create = screen.getByRole("button", {
      name: "Maximum of 10 watchlists reached",
    });
    expect(create.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(create);
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });
});
