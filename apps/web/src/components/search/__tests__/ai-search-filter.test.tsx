import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  configureAiFilter: vi.fn(),
  createAiFilteredWatchlist: vi.fn(),
  disableAiFilter: vi.fn(),
  session: { isLoggedIn: true, isPending: false },
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>
      {children as React.ReactNode}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/lib/actions/ai-filter", () => ({
  configureAiFilter: (...args: unknown[]) => mocks.configureAiFilter(...args),
  createAiFilteredWatchlist: (...args: unknown[]) =>
    mocks.createAiFilteredWatchlist(...args),
  disableAiFilter: (...args: unknown[]) => mocks.disableAiFilter(...args),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

import { AiSearchFilter } from "../ai-search-filter";

describe("AiSearchFilter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    Object.defineProperty(window, "scrollY", {
      configurable: true,
      value: 0,
    });
    mocks.session.isLoggedIn = true;
    mocks.session.isPending = false;
    mocks.disableAiFilter.mockResolvedValue({ ok: true });
    window.history.replaceState({}, "", "/en/explore?q=engineer");
  });

  it("keeps the collapsed trigger free of invisible subscription-badge spacing", () => {
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={12}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Narrow down search" });
    expect(trigger.textContent).toBe("Narrow down search");
    expect(screen.queryByText("Pro")).toBeNull();
  });

  it("right-aligns and viewport-constrains the editor when requested", () => {
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={12}
        align="right"
        watchlistId="11111111-1111-4111-8111-111111111111"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    const editor = screen.getByRole("region", {
      name: "Narrow down this search",
    });
    expect(editor.className).toContain("sm:right-0");
    expect(editor.className).not.toContain("sm:left-0");
    expect(editor.className).toContain("sm:max-h-[calc(100vh-6rem)]");
    expect(editor.className).toContain("sm:overflow-y-auto");
    expect(screen.queryByText("Pro")).toBeNull();
  });

  it("never lets an eligible search bypass the paid entitlement", () => {
    mocks.session.isLoggedIn = false;
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={24}
        createsWatchlist
        watchlistDraft={{
          title: "Backend",
          companyIds: [],
          filters: { anyCompany: true },
          isPublic: false,
        }}
      />,
    );

    expect(screen.queryByLabelText("What should make a job a match?")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    expect(screen.getByText("Narrowing is a Pro feature")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create watchlist" })).toBeNull();
  });

  it("opens narrowed results in the in-page result stack", () => {
    const onDrawerOpenChange = vi.fn();
    render(
      <div className="grid">
        <AiSearchFilter
          isSubscribed
          hasSearchFilters
          candidateCount={12}
          watchlistId="11111111-1111-4111-8111-111111111111"
          initialQuery="Backend roles"
          narrowedResultCount={12}
          presentation="drawer"
          onDrawerOpenChange={onDrawerOpenChange}
          drawerContent={(isOpen) => (
            <div>{isOpen ? "Visible narrowed result" : "Paused narrowed result"}</div>
          )}
          onStateChange={vi.fn()}
        />
      </div>,
    );
    const control = screen.getByRole("complementary", { name: "Precise matching" });
    expect(control.textContent).toContain("Narrowed results");
    expect(control.textContent).toContain("12 matches");
    expect(control.textContent).not.toContain("Backend roles");
    const trigger = screen.getByRole("button", { name: "View" });
    expect(trigger.className).not.toContain("fixed");
    fireEvent.click(trigger);

    const editor = screen.getByRole("region", { name: "Narrowed results" });
    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(true);
    expect(editor.className).not.toContain("fixed");
    expect(control.textContent).not.toContain("12 matches");
    expect(screen.getByRole("button", { name: "All results" })).toBeTruthy();
    expect(screen.getByText("Visible narrowed result")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit matching criteria" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove matching criteria" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "All results" }));
    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole("region", { name: "Narrowed results" })).toBeNull();
    expect(screen.getByText("Paused narrowed result")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "View" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit matching criteria" }));
    expect((screen.getByLabelText(
      "What should make a job a match?",
    ) as HTMLTextAreaElement).value).toBe("Backend roles");
    expect(screen.getByText("Visible narrowed result")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("What should make a job a match?")).toBeNull();
    expect(screen.getByRole("button", { name: "All results" })).toBeTruthy();
  });

  it("shows shared narrowed results without owner mutation controls", () => {
    render(
      <AiSearchFilter
        isSubscribed={false}
        readOnly
        hasSearchFilters
        candidateCount={120}
        watchlistId="11111111-1111-4111-8111-111111111111"
        initialQuery="Backend roles"
        narrowedResultCount={12}
        presentation="drawer"
        drawerContent={<div>Shared narrowed match</div>}
      />,
    );

    const control = screen.getByRole("complementary", { name: "Precise matching" });
    expect(control.textContent).toContain("Narrowed results");
    expect(control.textContent).toContain("12 matches");
    expect(control.textContent).not.toContain("Pro");

    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(screen.getByText("Shared narrowed match")).toBeTruthy();
    expect(screen.getByText("Backend roles")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit matching criteria" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove matching criteria" })).toBeNull();
  });

  it("keeps an existing narrowed feed closed after its owner loses Pro", () => {
    window.history.replaceState(
      {},
      "",
      "/en/watchlists/11111111-1111-4111-8111-111111111111?narrow=1",
    );
    const onDrawerOpenChange = vi.fn();
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={120}
        watchlistId="11111111-1111-4111-8111-111111111111"
        initialQuery="Backend roles"
        narrowedResultCount={12}
        presentation="drawer"
        onDrawerOpenChange={onDrawerOpenChange}
        drawerContent={<div>Locked narrowed match</div>}
      />,
    );

    const control = screen.getByRole("complementary", { name: "Precise matching" });
    expect(control.textContent).toContain("Narrowed results");
    expect(control.textContent).toContain("Pro");
    expect(screen.getByRole("button", { name: "Explore Pro" })).toBeTruthy();
    expect(screen.queryByText("Locked narrowed match")).toBeNull();
    expect(screen.queryByRole("region", { name: "Narrowed results" })).toBeNull();
    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(false);
    expect(window.location.search).toBe("");
  });

  it("keeps broad results active while the compact setup form is open", () => {
    const onDrawerOpenChange = vi.fn();
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={12}
        watchlistId="11111111-1111-4111-8111-111111111111"
        presentation="drawer"
        onDrawerOpenChange={onDrawerOpenChange}
        onStateChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Set up" }));

    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole("region", { name: "Narrow results precisely" })).toBeTruthy();
    expect(screen.getByLabelText("What should make a job a match?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Apply" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.queryByText("Narrow down this search")).toBeNull();
    expect(screen.queryByText("Pro")).toBeNull();
    expect(screen.queryByRole("button", { name: "Close precise matching" })).toBeNull();
  });

  it("shows a minimal reminder after sustained downward scrolling", () => {
    const onDrawerOpenChange = vi.fn();
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={120}
        watchlistId="11111111-1111-4111-8111-111111111111"
        presentation="drawer"
        onDrawerOpenChange={onDrawerOpenChange}
        onStateChange={vi.fn()}
      />,
    );

    Object.defineProperty(window, "scrollY", { configurable: true, value: 300 });
    fireEvent.scroll(window);
    expect(screen.queryByRole("dialog", { name: "Narrow results reminder" })).toBeNull();

    Object.defineProperty(window, "scrollY", { configurable: true, value: 700 });
    fireEvent.scroll(window);
    const reminder = screen.getByRole("dialog", { name: "Narrow results reminder" });
    expect(reminder.textContent).toContain("Narrow these results");
    expect(reminder.textContent).toContain("Describe what matters and focus this feed.");

    fireEvent.click(within(reminder).getByRole("button", { name: "Narrow results" }));
    expect(screen.queryByRole("dialog", { name: "Narrow results reminder" })).toBeNull();
    expect(screen.getByLabelText("What should make a job a match?")).toBeTruthy();
    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("uses the scroll reminder as a compact Pro prompt for free accounts", () => {
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={120}
        watchlistId="11111111-1111-4111-8111-111111111111"
        presentation="drawer"
      />,
    );

    Object.defineProperty(window, "scrollY", { configurable: true, value: 700 });
    fireEvent.scroll(window);

    const reminder = screen.getByRole("dialog", { name: "Narrow results reminder" });
    expect(reminder.textContent).toContain("Narrow these results");
    expect(reminder.textContent).toContain("Pro");
    expect(reminder.textContent).toContain(
      "Evaluate every posting against what matters to you.",
    );

    fireEvent.click(within(reminder).getByRole("button", { name: "Explore Pro" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/en/settings/billing?next=%2Fen%2Fexplore%3Fq%3Dengineer%26narrow%3D1",
    );
    expect(screen.queryByRole("dialog", { name: "Narrow results reminder" })).toBeNull();
  });

  it("does not show the floating setup reminder for an existing narrowed feed", () => {
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={120}
        watchlistId="11111111-1111-4111-8111-111111111111"
        initialQuery="Backend roles"
        narrowedResultCount={12}
        presentation="drawer"
        drawerContent={<div>Persisted narrowed result</div>}
      />,
    );

    Object.defineProperty(window, "scrollY", { configurable: true, value: 700 });
    fireEvent.scroll(window);

    expect(screen.queryByRole("dialog", { name: "Narrow results reminder" }))
      .toBeNull();
  });

  it("keeps a dismissed scroll reminder hidden for the browser session", () => {
    const props = {
      isSubscribed: true,
      hasSearchFilters: true,
      candidateCount: 120,
      watchlistId: "11111111-1111-4111-8111-111111111111",
      presentation: "drawer" as const,
      onStateChange: vi.fn(),
    };
    const { unmount } = render(<AiSearchFilter {...props} />);

    Object.defineProperty(window, "scrollY", { configurable: true, value: 700 });
    fireEvent.scroll(window);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss narrow results reminder" }));
    expect(window.sessionStorage.getItem(
      "jobseek:narrow-results-reminder:11111111-1111-4111-8111-111111111111",
    )).toBe("dismissed");

    unmount();
    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
    render(<AiSearchFilter {...props} />);
    Object.defineProperty(window, "scrollY", { configurable: true, value: 700 });
    fireEvent.scroll(window);
    expect(screen.queryByRole("dialog", { name: "Narrow results reminder" })).toBeNull();
  });

  it("uses the compact watchlist strip as a Pro teaser for free accounts", () => {
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={12}
        watchlistId="11111111-1111-4111-8111-111111111111"
        presentation="drawer"
      />,
    );

    const teaser = screen.getByRole("complementary", { name: "Precise matching" });
    expect(teaser.textContent).toContain("Narrow results precisely");
    expect(teaser.textContent).toContain("Pro");
    expect(teaser.textContent).toContain("Evaluate every posting against your request.");
    expect(screen.queryByLabelText("What should make a job a match?")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Explore Pro" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/en/settings/billing?next=%2Fen%2Fexplore%3Fq%3Dengineer%26narrow%3D1",
    );
  });

  it("shows the Pro gate instead of the Jev query field for free users", () => {
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={12}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    expect(screen.getByText("Precise matching is included with Pro")).toBeTruthy();
    expect(screen.queryByLabelText("What should make a job a match?")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "View Pro plan" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/en/settings/billing?next=%2Fen%2Fexplore%3Fq%3Dengineer%26narrow%3D1",
    );
  });

  it("restores the focused search after a signed-out viewer logs in", () => {
    mocks.session.isLoggedIn = false;
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={24}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    expect(screen.getByText(/Log in to check your plan or upgrade/))
      .toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/en/sign-in?next=%2Fen%2Fexplore%3Fq%3Dengineer%26narrow%3D1",
    );
  });

  it("stages the watchlist draft when a signed-out viewer continues", () => {
    mocks.session.isLoggedIn = false;
    const draft = {
      title: "Backend",
      companyIds: [],
      filters: { anyCompany: true },
      isPublic: false as const,
    };
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={24}
        createsWatchlist
        watchlistDraft={draft}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(mocks.push).toHaveBeenCalledWith(
      "/en/sign-in?next=%2Fen%2Fwatchlists",
    );
    expect(window.sessionStorage.length).toBe(1);
  });

  it("stays hidden until ordinary filters produce a bounded result set", () => {
    const { rerender } = render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters={false}
        candidateCount={120}
      />,
    );
    expect(screen.queryByRole("button", { name: "Narrow down search" })).toBeNull();

    rerender(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={10_001}
      />,
    );
    expect(screen.queryByRole("button", { name: "Narrow down search" })).toBeNull();

    rerender(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={10_000}
      />,
    );
    expect(screen.getByRole("button", { name: "Narrow down search" })).toBeTruthy();
  });

  it("creates a watchlist from a normalized prompt for an eligible focused search", async () => {
    mocks.createAiFilteredWatchlist.mockResolvedValue({
      id: "11111111-1111-4111-8111-111111111111",
      slug: "backend",
    });
    const draft = {
      title: "Backend",
      companyIds: [],
      filters: { anyCompany: true },
      isPublic: false as const,
    };
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={24}
        createsWatchlist
        watchlistDraft={draft}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    expect(screen.getByText(/create a watchlist from this search/i)).toBeTruthy();
    expect(
      screen.queryByText(/jobs in this feed will be evaluated/i),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Create watchlist" }).className,
    ).toContain("whitespace-nowrap");

    fireEvent.change(screen.getByLabelText("What should make a job a match?"), {
      target: { value: "  Backend roles   without management  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create watchlist" }));

    await waitFor(() => {
      expect(mocks.createAiFilteredWatchlist).toHaveBeenCalledWith({
        draft,
        query: "Backend roles without management",
      });
    });
  });

  it("creates, configures, and opens a precisely matched watchlist", async () => {
    mocks.createAiFilteredWatchlist.mockResolvedValue({
      id: "11111111-1111-4111-8111-111111111111",
      slug: "backend",
    });
    const draft = {
      title: "Backend",
      companyIds: [],
      filters: { anyCompany: true },
      isPublic: false as const,
    };
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={24}
        createsWatchlist
        watchlistDraft={draft}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Narrow down search" }));
    fireEvent.change(screen.getByLabelText("What should make a job a match?"), {
      target: { value: "Backend developer tools" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create watchlist" }));

    await waitFor(() => {
      expect(mocks.createAiFilteredWatchlist).toHaveBeenCalledWith({
        draft,
        query: "Backend developer tools",
      });
      expect(mocks.push).toHaveBeenCalledWith(
        "/en/watchlists/11111111-1111-4111-8111-111111111111",
      );
    });
  });

  it("restores, edits, and removes persisted matching criteria", async () => {
    const onStateChange = vi.fn();
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={24}
        watchlistId="11111111-1111-4111-8111-111111111111"
        initialQuery="Backend roles without management"
        onStateChange={onStateChange}
      />,
    );

    expect(screen.getByText("Backend roles without management")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Narrow down search" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edit matching criteria" }));
    expect((screen.getByLabelText(
      "What should make a job a match?",
    ) as HTMLTextAreaElement).value).toBe("Backend roles without management");
    fireEvent.click(screen.getByRole("button", { name: "Close precise matching" }));

    fireEvent.click(screen.getByRole("button", { name: "Remove matching criteria" }));
    await waitFor(() => {
      expect(mocks.disableAiFilter).toHaveBeenCalledWith(
        "11111111-1111-4111-8111-111111111111",
      );
      expect(onStateChange).toHaveBeenCalledWith(null);
    });
  });
});
