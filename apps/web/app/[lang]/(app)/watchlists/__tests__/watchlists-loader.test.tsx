import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  load: vi.fn(),
  session: {
    user: { id: "user-1", username: "alice" } as {
      id: string;
      username: string | null;
    } | null,
    isPending: false,
  },
}));

vi.mock("@/lib/actions/watchlists", () => ({
  getUserWatchlistsWithLimit: (...args: unknown[]) => mocks.load(...args),
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => mocks.session,
}));

vi.mock("../watchlists-page", () => ({
  WatchlistsPage: ({
    initialWatchlists,
    username,
  }: {
    initialWatchlists: unknown[];
    username: string | null;
  }) => (
    <div
      data-testid="watchlists-page"
      data-count={initialWatchlists.length}
      data-username={username ?? ""}
    />
  ),
}));

import { WatchlistsLoader } from "../watchlists-loader";

describe("WatchlistsLoader recovery (#5896)", () => {
  beforeEach(() => {
    mocks.load.mockReset();
    mocks.session.user = { id: "user-1", username: "alice" };
    mocks.session.isPending = false;
  });

  it("retries one transient failure and renders the recovered data", async () => {
    mocks.load
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        watchlists: [{ id: "wl-1" }],
        limitReached: false,
      });

    render(<WatchlistsLoader locale="en" />);

    const page = await screen.findByTestId("watchlists-page");
    expect(page.getAttribute("data-count")).toBe("1");
    expect(page.getAttribute("data-username")).toBe("alice");
    expect(mocks.load).toHaveBeenCalledTimes(2);
  });

  it("replaces the permanent spinner with an error and a working retry", async () => {
    mocks.load
      .mockRejectedValueOnce(new Error("cold failure"))
      .mockRejectedValueOnce(new Error("retry failure"));

    render(<WatchlistsLoader locale="en" />);

    const retry = await screen.findByRole("button", { name: /try again/i });
    expect(screen.getByText(/couldn't load your watchlists/i)).toBeTruthy();

    mocks.load.mockResolvedValueOnce({ watchlists: [], limitReached: false });
    fireEvent.click(retry);

    await waitFor(() => {
      expect(screen.getByTestId("watchlists-page")).toBeTruthy();
    });
    expect(mocks.load).toHaveBeenCalledTimes(3);
  });
});
