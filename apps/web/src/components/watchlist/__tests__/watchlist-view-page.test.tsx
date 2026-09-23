import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  jobListProps: vi.fn(),
  updateWatchlist: vi.fn(),
  copySharedWatchlist: vi.fn(),
  copyTextToClipboard: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  isLoggedIn: true,
  isPending: false,
  plan: "unlimited" as "free" | "unlimited",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { prefetch?: boolean }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({
    isLoggedIn: mocks.isLoggedIn,
    isPending: mocks.isPending,
    plan: mocks.plan,
    refresh: mocks.refresh,
  }),
}));

vi.mock("@/lib/actions/watchlists", () => ({
  updateWatchlist: (...args: unknown[]) => mocks.updateWatchlist(...args),
  copySharedWatchlist: (...args: unknown[]) => mocks.copySharedWatchlist(...args),
}));

vi.mock("@/lib/actions/ai-filter", () => ({
  configureAiFilter: vi.fn(),
  createAiFilteredWatchlist: vi.fn(),
}));

vi.mock("@/lib/copy-text-to-clipboard", () => ({
  copyTextToClipboard: (...args: unknown[]) => mocks.copyTextToClipboard(...args),
}));

vi.mock("@/components/providers/SalaryDisplayProvider", () => ({
  useSalaryRates: () => [
    { currency: "EUR", toEur: 1 },
    { currency: "USD", toEur: 0.92 },
  ],
}));

vi.mock("@/components/watchlist/company-pill", () => ({
  CompanyPill: ({
    company,
    onRemove,
  }: {
    company: { id: string; name: string };
    onRemove?: (id: string) => void;
  }) => (
    <button type="button" onClick={() => onRemove?.(company.id)}>
      Remove {company.name}
    </button>
  ),
}));

vi.mock("@/components/watchlist/company-search-modal", () => ({
  CompanySearchModal: () => null,
}));

vi.mock("@/components/watchlist/watchlist-action-bar", () => ({
  WatchlistActionBar: () => <div data-testid="action-bar" />,
}));

vi.mock("@/components/watchlist/watchlist-job-list", () => ({
  WatchlistJobList: (props: {
    filters: { salaryMin?: number; salaryMax?: number };
    drawerControl?: React.ReactNode;
  }) => {
    mocks.jobListProps(props);
    return <div data-testid="job-list">{props.drawerControl}</div>;
  },
}));

vi.mock("@/components/search/filter-pills-readonly", () => ({
  FilterPillsReadOnly: () => null,
}));

vi.mock("@/components/search/advanced-search-panel", () => ({
  AdvancedSearchPanel: ({
    onSalaryChange,
    onExperienceChange,
  }: {
    onSalaryChange?: (currency: string, min: number | undefined, max: number | undefined) => void;
    onExperienceChange?: (min: number | undefined, max: number | undefined) => void;
  }) => (
    <>
      <button
        type="button"
        onClick={() => onSalaryChange?.("USD", 200_000, undefined)}
      >
        Apply salary
      </button>
      <button
        type="button"
        onClick={() => onSalaryChange?.("USD", undefined, undefined)}
      >
        Clear salary
      </button>
      <button
        type="button"
        onClick={() => onExperienceChange?.(undefined, undefined)}
      >
        Clear experience
      </button>
    </>
  ),
}));

import { WatchlistViewPage } from "../watchlist-view-page";
import {
  readPendingWatchlists,
  stagePendingWatchlistEntry,
} from "@/lib/pending-watchlist";

const detail = {
  id: "11111111-1111-4111-8111-111111111111",
  slug: "us-roles",
  title: "US roles",
  description: "A focused list",
  isPublic: false,
  alertsEnabled: false,
  filters: {
    anyCompany: true,
    salaryCurrency: "USD",
    salaryMin: 100_000,
  },
  sourceWatchlistId: null,
  createdAt: "2026-09-10T00:00:00.000Z",
  owner: {
    id: "user-1",
    username: "owner",
    displayUsername: "owner",
    name: "Owner",
  },
  companies: [] as Array<{
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  }>,
};

function renderPage(
  isOwner = true,
  detailOverride: Partial<typeof detail> = {},
  limitReached = false,
  initialTotal = 0,
  initialAiFilterState: React.ComponentProps<typeof WatchlistViewPage>["initialAiFilterState"] = null,
  sessionWatchlistId?: string,
) {
  return render(
    <WatchlistViewPage
      data={{
        detail: { ...detail, ...detailOverride },
        isOwner,
        limitReached,
        postings: [],
        total: initialTotal,
        truncated: false,
        yearTotal: 0,
        searchUnavailable: false,
        resolvedLocations: [],
        resolvedOccupations: [],
        resolvedSeniorities: [],
        resolvedTechnologies: [],
        jobLanguages: [],
        languages: ["en"],
        browserPostingFilters: {
          companyIds: [],
          anyCompany: true,
          salaryMin: 92_000,
          languages: ["en"],
        },
      }}
      locale="en"
      initialAiFilterState={initialAiFilterState}
      sessionWatchlistId={sessionWatchlistId}
    />,
  );
}

describe("WatchlistViewPage private detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.updateWatchlist.mockResolvedValue({ ok: true });
    mocks.copySharedWatchlist.mockResolvedValue({
      id: "22222222-2222-4222-8222-222222222222",
      slug: "us-roles-copy",
    });
    mocks.copyTextToClipboard.mockResolvedValue(undefined);
    mocks.refresh.mockResolvedValue(undefined);
    mocks.isLoggedIn = true;
    mocks.isPending = false;
    mocks.plan = "unlimited";
    window.sessionStorage.clear();
  });

  it("keeps ordinary filters in the configuration panel and precise matching with the results", () => {
    renderPage(true, {}, false, 24, {
      watchlistId: detail.id,
      enabled: true,
      entitled: true,
      query: "Backend roles",
      queryRevision: 1,
      queryVersionId: "33333333-3333-4333-8333-333333333333",
      status: "caught_up",
      counts: { accepted: 4, rejected: 20, total: 24 },
      progress: {
        selectionOffset: 0,
        scannedCount: 24,
        completedCount: 24,
        stopReason: "caught_up",
      },
      lastCaughtUpAt: "2026-09-22T00:00:00.000Z",
      latestEventSequence: 1,
    });

    const filterControl = screen.getByRole("button", { name: "Apply salary" });
    const filterGrid = filterControl.parentElement;
    expect(filterGrid?.className).toContain("space-y-3");

    const narrowButton = screen.getByRole("button", { name: "View" });
    expect(screen.getAllByTestId("job-list")[0]?.contains(narrowButton)).toBe(true);
  });

  it("keeps owner mutations hidden until the hydrated client confirms the session", async () => {
    mocks.isLoggedIn = false;
    renderPage(true);

    expect(screen.getByRole("heading", { name: "US roles" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "US roles" })).toBeNull();
    expect(screen.queryByTestId("action-bar")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add description" })).toBeNull();
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledOnce());
  });

  it("reuses the owned watchlist view while persisting an anonymous session watchlist locally", async () => {
    mocks.isLoggedIn = false;
    const entry = stagePendingWatchlistEntry({
      kind: "create",
      draft: {
        title: detail.title,
        description: detail.description,
        companyIds: [],
        filters: detail.filters,
        isPublic: false,
      },
    })!;

    renderPage(true, { id: entry.id }, false, 0, null, entry.id);

    expect(screen.getByRole("status").textContent).toContain("Saved in this browser");
    expect(screen.queryByText("Log in to keep")).toBeNull();
    expect(screen.getByTestId("action-bar")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "US roles" }));
    const titleInput = screen.getByRole("textbox");
    fireEvent.change(titleInput, { target: { value: "Swiss internships" } });
    fireEvent.blur(titleInput);

    await waitFor(() => expect(readPendingWatchlists()[0]?.intent).toEqual(
      expect.objectContaining({
        kind: "create",
        draft: expect.objectContaining({ title: "Swiss internships" }),
      }),
    ));
    expect(mocks.updateWatchlist).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("shows a saved narrowed feed on a shared watchlist even when the rollout flag is off", () => {
    renderPage(false, {}, false, 24, {
      watchlistId: detail.id,
      enabled: true,
      entitled: true,
      query: "Backend roles",
      queryRevision: 1,
      queryVersionId: "33333333-3333-4333-8333-333333333333",
      status: "caught_up",
      counts: { accepted: 4, rejected: 20, total: 24 },
      progress: {
        selectionOffset: 0,
        scannedCount: 24,
        completedCount: 24,
        stopReason: "caught_up",
      },
      lastCaughtUpAt: "2026-09-22T00:00:00.000Z",
      latestEventSequence: 1,
    });

    expect(screen.getByRole("button", { name: "View" })).toBeTruthy();
    expect(mocks.jobListProps.mock.calls.some(
      ([props]) => (props as { sharedSnapshot?: boolean }).sharedSnapshot === true,
    )).toBe(true);
  });

  it("exposes populated title and description edits as keyboard-focusable buttons", () => {
    renderPage();

    const titleButton = screen.getByRole("button", { name: "US roles" });
    const descriptionButton = screen.getByRole("button", { name: "A focused list" });
    expect(titleButton.getAttribute("type")).toBe("button");
    expect(descriptionButton.getAttribute("type")).toBe("button");

    fireEvent.click(titleButton);
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("keeps initial and edited browser searches in EUR while persisting display values", async () => {
    vi.useFakeTimers();
    try {
      renderPage();

      expect(mocks.jobListProps.mock.lastCall?.[0].filters.salaryMin).toBe(92_000);
      fireEvent.click(screen.getByRole("button", { name: "Apply salary" }));
      expect(mocks.jobListProps.mock.lastCall?.[0].filters.salaryMin).toBe(184_000);
      await vi.advanceTimersByTimeAsync(500);
      expect(mocks.updateWatchlist).toHaveBeenCalledWith(expect.objectContaining({
        filters: expect.objectContaining({
          salaryCurrency: "USD",
          salaryMin: 200_000,
        }),
      }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("rolls back a thrown title save and always clears the saving state", async () => {
    mocks.updateWatchlist.mockRejectedValueOnce(new Error("database unavailable"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "US roles" }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Platform roles" } });
    fireEvent.blur(input);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not save your changes.",
    );
    expect(screen.getByRole("button", { name: "US roles" })).toBeTruthy();
    expect(document.querySelector(".animate-spin")).toBeNull();
  });

  it("submits a title only once when Enter also causes blur", async () => {
    let resolveSave: ((value: { slug: string }) => void) | undefined;
    mocks.updateWatchlist.mockReturnValueOnce(new Promise((resolve) => {
      resolveSave = resolve;
    }));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "US roles" }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Platform roles" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);

    expect(mocks.updateWatchlist).toHaveBeenCalledTimes(1);
    resolveSave?.({ slug: "platform-roles" });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Platform roles" })).toBeTruthy();
    });
  });

  it("cancels later title and description edits back to the last successful save", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "US roles" }));
    let editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "Platform roles" } });
    fireEvent.blur(editor);
    await screen.findByRole("button", { name: "Platform roles" });

    fireEvent.click(screen.getByRole("button", { name: "Platform roles" }));
    editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "Discard this title" } });
    fireEvent.keyDown(editor, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Platform roles" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "A focused list" }));
    editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "Persisted description" } });
    fireEvent.blur(editor);
    await screen.findByRole("button", { name: "Persisted description" });

    fireEvent.click(screen.getByRole("button", { name: "Persisted description" }));
    editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "Discard this description" } });
    fireEvent.keyDown(editor, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Persisted description" })).toBeTruthy();
  });

  it("rolls back a description when the server returns an error", async () => {
    mocks.updateWatchlist.mockResolvedValueOnce({ error: "invalid_input" });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "A focused list" }));
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "Changed description" } });
    fireEvent.blur(textarea);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not save your changes.",
    );
    expect(screen.getByRole("button", { name: "A focused list" })).toBeTruthy();
  });

  it("rolls back an optimistic company removal when persistence fails", async () => {
    mocks.updateWatchlist.mockResolvedValueOnce({ error: "invalid_input" });
    renderPage(true, {
      filters: { ...detail.filters, anyCompany: false },
      companies: [{ id: "company-1", name: "Acme", slug: "acme", icon: null }],
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Acme" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not save your changes.",
    );
    expect(mocks.updateWatchlist).toHaveBeenCalledWith({
      watchlistId: detail.id,
      companyIds: [],
    });
    expect(screen.getByRole("button", { name: "Remove Acme" })).toBeTruthy();
  });

  it("flushes a pending filter save when Back navigation unmounts the detail", async () => {
    vi.useFakeTimers();
    try {
      const { unmount } = renderPage();

      fireEvent.click(screen.getByRole("button", { name: "Apply salary" }));
      unmount();
      await Promise.resolve();
      await Promise.resolve();

      expect(mocks.updateWatchlist).toHaveBeenCalledWith(expect.objectContaining({
        filters: expect.objectContaining({ salaryCurrency: "USD", salaryMin: 200_000 }),
      }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("persists clearing optional salary and experience bounds", async () => {
    vi.useFakeTimers();
    try {
      renderPage(true, {
        filters: {
          ...detail.filters,
          salaryMax: 140_000,
          experienceMin: 2,
          experienceMax: 6,
        } as typeof detail.filters,
      });

      fireEvent.click(screen.getByRole("button", { name: "Clear salary" }));
      await vi.advanceTimersByTimeAsync(500);
      await Promise.resolve();
      expect(mocks.updateWatchlist).toHaveBeenLastCalledWith(expect.objectContaining({
        filters: expect.objectContaining({
          salaryMin: undefined,
          salaryMax: undefined,
        }),
      }));

      fireEvent.click(screen.getByRole("button", { name: "Clear experience" }));
      await vi.advanceTimersByTimeAsync(500);
      await Promise.resolve();
      expect(mocks.updateWatchlist).toHaveBeenLastCalledWith(expect.objectContaining({
        filters: expect.objectContaining({
          experienceMin: undefined,
          experienceMax: undefined,
        }),
      }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("clones a shared watchlist into the viewer account and opens the private copy", async () => {
    renderPage(false);

    fireEvent.click(screen.getByRole("button", { name: "Clone" }));

    await vi.waitFor(() => {
      expect(mocks.copySharedWatchlist).toHaveBeenCalledWith(detail.id);
      expect(mocks.push).toHaveBeenCalledWith(
        "/en/watchlists/22222222-2222-4222-8222-222222222222",
      );
    });
    expect(screen.queryByTestId("action-bar")).toBeNull();
  });

  it("warns a free viewer that a cloned narrowed watchlist keeps only standard filters", async () => {
    mocks.plan = "free";
    const aiState = {
      watchlistId: detail.id,
      enabled: true,
      entitled: true,
      query: "Backend roles",
      queryRevision: 1,
      queryVersionId: "22222222-2222-4222-8222-222222222222",
      status: "caught_up" as const,
      counts: { accepted: 12, rejected: 88, total: 100 },
      progress: { selectionOffset: 0, scannedCount: 100, completedCount: 100, stopReason: "caught_up" },
      lastCaughtUpAt: "2026-09-22T00:00:00.000Z",
      latestEventSequence: 2,
    };
    renderPage(false, {}, false, 100, aiState);

    fireEvent.click(screen.getByRole("button", { name: "Clone" }));

    expect(screen.getByRole("alertdialog").textContent).toContain(
      "Clone without narrowed results?",
    );
    expect(screen.getByRole("alertdialog").textContent).toContain(
      "you won’t be able to set up or view its narrowed feed",
    );
    expect(mocks.copySharedWatchlist).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Clone standard filters" }));
    await waitFor(() => {
      expect(mocks.copySharedWatchlist).toHaveBeenCalledWith(detail.id);
    });
  });

  it("copies the clean shared URL without mutating or cloning the watchlist", async () => {
    const originalUrl = window.location.href;
    window.history.replaceState(
      {},
      "",
      `/en/watchlists/${detail.id}?show=33333333-3333-4333-8333-333333333333#details`,
    );

    try {
      renderPage(false);

      const shareButton = screen.getByRole("button", { name: "Share" });
      fireEvent.click(shareButton);

      await waitFor(() => {
        expect(mocks.copyTextToClipboard).toHaveBeenCalledWith(
          `${window.location.origin}/en/watchlists/${detail.id}`,
        );
      });
      expect((await screen.findByRole("status")).textContent).toBe("Link copied");
      expect(mocks.copySharedWatchlist).not.toHaveBeenCalled();
      expect(screen.queryByTestId("action-bar")).toBeNull();
    } finally {
      window.history.replaceState({}, "", originalUrl);
    }
  });

  it("keeps Share focused, rejects duplicate pending copies, and permits retry after failure", async () => {
    let rejectCopy: ((error: Error) => void) | undefined;
    mocks.copyTextToClipboard.mockReturnValueOnce(new Promise((_, reject) => {
      rejectCopy = reject;
    }));
    renderPage(false);

    const shareButton = screen.getByRole("button", { name: "Share" });
    shareButton.focus();
    fireEvent.click(shareButton);
    fireEvent.click(shareButton);
    expect(mocks.copyTextToClipboard).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(shareButton);

    rejectCopy?.(new Error("clipboard unavailable"));
    expect((await screen.findByRole("status")).textContent).toBe("Copy failed");

    mocks.copyTextToClipboard.mockResolvedValueOnce(undefined);
    fireEvent.click(shareButton);
    await waitFor(() => expect(mocks.copyTextToClipboard).toHaveBeenCalledTimes(2));
    expect((await screen.findByRole("status")).textContent).toBe("Link copied");
  });

  it("keeps Share and Clone in one aligned row when cloning fails", async () => {
    mocks.copySharedWatchlist.mockResolvedValueOnce({ error: "unknown" });
    renderPage(false);

    const shareButton = screen.getByRole("button", { name: "Share" });
    const cloneButton = screen.getByRole("button", { name: "Clone" });
    expect(shareButton.parentElement).toBe(cloneButton.parentElement);

    fireEvent.click(cloneButton);
    const error = await screen.findByRole("alert");
    expect(error.previousElementSibling).toBe(shareButton.parentElement);
    expect(shareButton.parentElement).toBe(cloneButton.parentElement);
  });

  it("disables Clone at the known cap and explains it without calling the server", async () => {
    renderPage(false, {}, true);

    const cloneButton = screen.getByRole("button", { name: "Clone" });
    expect(cloneButton.getAttribute("aria-disabled")).toBe("true");
    expect(cloneButton.className).toContain("!opacity-50");

    fireEvent.focus(cloneButton);
    const limitStatus = await screen.findByRole("status");
    expect(limitStatus.textContent).toBe("Maximum of 10 watchlists reached");
    expect(limitStatus.parentElement?.className).toContain("bg-warning-bg");

    fireEvent.click(cloneButton);

    expect(mocks.copySharedWatchlist).not.toHaveBeenCalled();
    expect(screen.queryByText("Could not clone this watchlist.")).toBeNull();
  });

  it("turns a clone limit race into the same disabled tooltip state", async () => {
    mocks.copySharedWatchlist.mockResolvedValueOnce({ error: "limit_reached" });
    renderPage(false);

    const cloneButton = screen.getByRole("button", { name: "Clone" });
    fireEvent.click(cloneButton);

    expect((await screen.findByRole("status")).textContent)
      .toBe("Maximum of 10 watchlists reached");
    expect(cloneButton.getAttribute("aria-disabled")).toBe("true");

    fireEvent.click(cloneButton);
    expect(mocks.copySharedWatchlist).toHaveBeenCalledTimes(1);
  });

  it("stages an anonymous clone without forcing an immediate sign-in", () => {
    mocks.isLoggedIn = false;
    renderPage(false);

    fireEvent.click(screen.getByRole("button", { name: "Clone" }));
    expect(screen.getByRole("button", { name: "Share" })).toBeTruthy();
    expect(mocks.push).toHaveBeenCalledWith(
      expect.stringMatching(/^\/en\/watchlists\/[0-9a-f-]+$/),
    );
    expect(mocks.copySharedWatchlist).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(1);
  });

  it("keeps anonymous cloning available while account state is resolving", () => {
    mocks.isLoggedIn = false;
    mocks.isPending = true;
    renderPage(false);

    const cloneButton = screen.getByRole("button", { name: "Clone" });
    expect(cloneButton.getAttribute("disabled")).toBeNull();
    expect(screen.queryByText("Checking account…")).toBeNull();

    fireEvent.click(cloneButton);
    expect(mocks.push).toHaveBeenCalledWith(
      expect.stringMatching(/^\/en\/watchlists\/[0-9a-f-]+$/),
    );
    expect(mocks.copySharedWatchlist).not.toHaveBeenCalled();
  });
});
