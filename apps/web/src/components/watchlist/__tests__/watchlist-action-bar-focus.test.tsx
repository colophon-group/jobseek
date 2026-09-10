import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  deleteWatchlist: vi.fn(),
  shareWatchlist: vi.fn(),
  toggleWatchlistAlerts: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, refresh: mocks.refresh }),
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
  shareWatchlist: mocks.shareWatchlist,
  toggleWatchlistAlerts: mocks.toggleWatchlistAlerts,
}));

import { WatchlistActionBar } from "../watchlist-action-bar";

function renderActionBar({
  alertsEnabled = false,
}: {
  alertsEnabled?: boolean;
} = {}) {
  return render(
    <WatchlistActionBar
      watchlistId="watchlist-1"
      alertsEnabled={alertsEnabled}
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
    mocks.shareWatchlist.mockResolvedValue({
      ok: true,
      url: "https://jseek.co/watchlists/11111111-1111-4111-8111-111111111111",
    });
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

  it("deletes and returns to the overview without a legacy selection action", async () => {
    const user = userEvent.setup();
    renderActionBar();

    const { dialog } = await openDeleteDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mocks.deleteWatchlist).toHaveBeenCalledWith("watchlist-1");
      expect(mocks.replace).toHaveBeenCalledWith("/en/watchlists");
    });
  });

  it("enables sharing, copies the canonical link, and confirms above the button", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    try {
      renderActionBar();
      await user.click(screen.getByRole("button", { name: "Share" }));

      expect(mocks.shareWatchlist).toHaveBeenCalledWith("watchlist-1");
      const status = await screen.findByRole("status");
      expect(writeText).toHaveBeenCalledWith(
        "https://jseek.co/watchlists/11111111-1111-4111-8111-111111111111",
      );
      expect(status.textContent).toBe("Link copied");
      expect(status.parentElement?.getAttribute("data-side")).toBe("top");
    } finally {
      if (clipboardDescriptor) {
        Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
      } else {
        Reflect.deleteProperty(navigator, "clipboard");
      }
    }
  });

  it("keeps the control panel mounted while only the alert icon shows progress", async () => {
    let resolveToggle: ((value: { enabled: boolean }) => void) | undefined;
    mocks.toggleWatchlistAlerts.mockReturnValueOnce(new Promise<{ enabled: boolean }>((resolve) => {
      resolveToggle = resolve;
    }));
    const user = userEvent.setup();
    renderActionBar();

    const alertButton = screen.getByRole("button", { name: "Enable alerts" });
    await user.click(alertButton);

    expect(mocks.toggleWatchlistAlerts).toHaveBeenCalledWith("watchlist-1");
    expect(screen.getByRole("button", { name: "Share" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    expect(alertButton.getAttribute("aria-busy")).toBe("true");
    expect(alertButton.querySelector("svg")?.getAttribute("class")).toContain("animate-spin");
    await user.click(alertButton);
    expect(mocks.toggleWatchlistAlerts).toHaveBeenCalledTimes(1);

    resolveToggle?.({ enabled: true });
    await waitFor(() => {
      expect(mocks.refresh).toHaveBeenCalledTimes(1);
      expect(alertButton.getAttribute("aria-busy")).toBeNull();
      expect(screen.getByRole("button", { name: "Disable alerts" })).toBeTruthy();
    });
  });

  it("keeps the alert control in place and reports a resolved mutation error", async () => {
    mocks.toggleWatchlistAlerts.mockResolvedValueOnce({ error: "notifications_paused" });
    const user = userEvent.setup();
    renderActionBar();

    await user.click(screen.getByRole("button", { name: "Enable alerts" }));

    const errorStatus = await screen.findByRole("status");
    expect(errorStatus.textContent).toBe("Could not update alerts");
    expect(errorStatus.parentElement?.className).toContain("bg-warning-bg");
    expect(screen.getByRole("button", { name: "Could not update alerts" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Share" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("reports a rejected alert mutation without unmounting the controls", async () => {
    mocks.toggleWatchlistAlerts.mockRejectedValueOnce(new Error("network unavailable"));
    const user = userEvent.setup();
    renderActionBar();

    await user.click(screen.getByRole("button", { name: "Enable alerts" }));

    expect((await screen.findByRole("status")).textContent).toBe("Could not update alerts");
    expect(screen.getByRole("button", { name: "Share" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("allows an existing alert to be disabled without subscription gating", async () => {
    mocks.toggleWatchlistAlerts.mockResolvedValueOnce({ enabled: false });
    const user = userEvent.setup();
    renderActionBar({ alertsEnabled: true });

    const disableButton = screen.getByRole("button", { name: "Disable alerts" });
    expect(disableButton.getAttribute("aria-disabled")).toBeNull();
    await user.click(disableButton);

    expect(mocks.toggleWatchlistAlerts).toHaveBeenCalledWith("watchlist-1");
    expect(await screen.findByRole("button", { name: "Enable alerts" })).toBeTruthy();
  });
});
