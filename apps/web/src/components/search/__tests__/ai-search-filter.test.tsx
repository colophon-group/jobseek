import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("shows the Pro gate instead of the Jev query field for free users", () => {
    render(
      <AiSearchFilter
        isSubscribed={false}
        hasSearchFilters
        candidateCount={12}
        demoState="free"
      />,
    );

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
    expect(screen.getByText("Your current search will be restored when you return."))
      .toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/en/sign-in?next=%2Fen%2Fexplore%3Fq%3Dengineer%26narrow%3D1",
    );
  });

  it("asks subscribed users to narrow searches above the candidate ceiling", () => {
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={12_500}
        demoState="too-broad"
      />,
    );

    expect(screen.getByText("Narrow the search first")).toBeTruthy();
    expect(screen.getByText(/12500 jobs match/)).toBeTruthy();
    expect(screen.getByText(/10000 or fewer/)).toBeTruthy();
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
    const onApply = vi.fn().mockResolvedValue(undefined);
    render(
      <AiSearchFilter
        isSubscribed
        hasSearchFilters
        candidateCount={24}
        demoState="eligible"
        createsWatchlist
        onApply={onApply}
      />,
    );

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
      expect(onApply).toHaveBeenCalledWith("Backend roles without management");
    });
    await waitFor(() => {
      expect(screen.queryByLabelText("What should make a job a match?")).toBeNull();
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
        demoState="eligible"
        createsWatchlist
        watchlistDraft={draft}
      />,
    );

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
