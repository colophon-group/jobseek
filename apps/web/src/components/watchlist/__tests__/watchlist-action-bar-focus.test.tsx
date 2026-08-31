import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  deleteWatchlist: vi.fn(),
  clearSelection: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: mocks.refresh }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({
    user: { username: "test-user" },
    isLoggedIn: true,
  }),
}));

vi.mock("@/lib/actions/watchlists", () => ({
  deleteWatchlist: mocks.deleteWatchlist,
  toggleWatchlistAlerts: vi.fn(),
}));

vi.mock("@/lib/actions/watchlist-selection", () => ({
  clearWatchlistSelection: mocks.clearSelection,
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

import { WatchlistActionBar } from "../watchlist-action-bar";

function renderActionBar() {
  return render(
    <WatchlistActionBar
      watchlistId="watchlist-1"
      alertsEnabled={false}
      isPaidPlan
    />,
  );
}

async function openDeleteDialog(user: ReturnType<typeof userEvent.setup>) {
  const trigger = screen.getByRole("button", { name: "Delete" });
  await user.click(trigger);
  const dialog = await screen.findByRole("alertdialog", {
    name: "Delete watchlist?",
  });
  return { dialog, trigger };
}

describe("WatchlistActionBar delete focus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.deleteWatchlist.mockResolvedValue({ ok: true });
    mocks.clearSelection.mockResolvedValue(undefined);
  });

  it("restores focus to Delete after Cancel", async () => {
    const user = userEvent.setup();
    renderActionBar();

    const { dialog, trigger } = await openDeleteDialog(user);
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    await waitFor(() => expect(document.activeElement).toBe(cancel));

    await user.click(cancel);

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("restores focus to Delete after Escape", async () => {
    const user = userEvent.setup();
    renderActionBar();

    const { trigger } = await openDeleteDialog(user);
    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("deletes, clears the active hint, and refreshes the canonical route", async () => {
    const user = userEvent.setup();
    renderActionBar();

    const { dialog } = await openDeleteDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mocks.deleteWatchlist).toHaveBeenCalledWith("watchlist-1");
      expect(mocks.clearSelection).toHaveBeenCalledOnce();
      expect(mocks.refresh).toHaveBeenCalledOnce();
    });
  });
});
