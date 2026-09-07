import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  copyWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }),
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
  copyWatchlist: mocks.copyWatchlist,
  deleteWatchlist: mocks.deleteWatchlist,
  updateWatchlist: vi.fn(),
}));

import { WatchlistActionBar } from "../watchlist-action-bar";

function renderActionBar({ limitReached = false }: { limitReached?: boolean } = {}) {
  return render(
    <WatchlistActionBar
      watchlistId="watchlist-1"
      isOwner
      isPublic={false}
      alertsEnabled={false}
      isPaidPlan
      limitReached={limitReached}
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
    mocks.copyWatchlist.mockResolvedValue({ slug: "copy" });
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

  it("still deletes and navigates back to Watchlists", async () => {
    const user = userEvent.setup();
    renderActionBar();

    const { dialog } = await openDeleteDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mocks.deleteWatchlist).toHaveBeenCalledWith("watchlist-1");
      expect(mocks.push).toHaveBeenCalledWith("/en/watchlists");
    });
  });
});

describe("WatchlistActionBar universal copy limit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the neutral notice without copying when the page is already at the limit", async () => {
    const user = userEvent.setup();
    renderActionBar({ limitReached: true });

    const trigger = screen.getByRole("button", { name: "Mirror" });
    await user.click(trigger);

    await screen.findByRole("dialog", { name: "10-watchlist limit" });
    expect(mocks.copyWatchlist).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Got it" }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("shows the neutral notice when a concurrent copy loses the final slot", async () => {
    const user = userEvent.setup();
    mocks.copyWatchlist.mockResolvedValue({ error: "limit_reached" });
    renderActionBar();

    const replacedTrigger = screen.getByRole("button", { name: "Mirror" });
    await user.click(replacedTrigger);

    await screen.findByRole("dialog", { name: "10-watchlist limit" });
    expect(mocks.push).not.toHaveBeenCalled();

    const replacementTrigger = screen.getByRole("button", {
      name: "Mirror",
      hidden: true,
    });
    expect(replacementTrigger).not.toBe(replacedTrigger);
    await user.click(screen.getByRole("button", { name: "Got it" }));
    await waitFor(() => expect(document.activeElement).toBe(replacementTrigger));
  });
});

describe("WatchlistActionBar notification availability", () => {
  it("does not offer alert delivery before the notification system ships", () => {
    renderActionBar();

    expect(screen.queryByRole("button", { name: /alerts/i })).toBeNull();
  });
});
