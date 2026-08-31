/**
 * Tests for the watchlist CreateWatchlistCard disabled state — issue
 * #3036 sub-bug 2. The card must:
 *   1. dim visually (`opacity-50`) when `disabled`
 *   2. not invoke `onClick` when `disabled` (so it can't create a 2nd
 *      watchlist on a free plan)
 *   3. open the upgrade modal instead, telling the user why nothing
 *      happened
 */
import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@/test-utils/lingui-mock";

import { CreateWatchlistCard, WatchlistCard } from "../watchlist-card";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("WatchlistCard selection", () => {
  it("selects in place and exposes the active state", () => {
    const onSelect = vi.fn();

    render(
      <WatchlistCard
        active
        selecting={false}
        onSelect={onSelect}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: null,
          isPublic: true,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: 34,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    const button = screen.getByRole("button", { name: /maang\+/i });
    fireEvent.click(button);

    expect(onSelect).toHaveBeenCalledOnce();
    expect(button.getAttribute("aria-pressed")).toBe("true");
  });

  it("shows a useful company count until the live job count arrives", () => {
    render(
      <WatchlistCard
        active={false}
        selecting={false}
        onSelect={() => {}}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: null,
          isPublic: true,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: null,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    expect(screen.getByText("12 companies")).toBeTruthy();
  });
});

describe("CreateWatchlistCard (issue #3036)", () => {
  it("applies dimmed styling when disabled", () => {
    render(<CreateWatchlistCard onClick={() => {}} disabled />);
    // The button is the Tooltip trigger when disabled; find by accessible
    // text "Create".
    const btn = screen.getByRole("button", { name: /maximum of 10/i });
    expect(btn.className).toContain("opacity-50");
  });

  it("does not call onClick when disabled (gating intact)", () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} disabled />);
    const btn = screen.getByRole("button", { name: /maximum of 10/i });
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("announces the account-wide ceiling instead of an upgrade state", () => {
    render(<CreateWatchlistCard onClick={() => {}} disabled />);
    const btn = screen.getByRole("button", { name: /maximum of 10/i });
    expect(btn.getAttribute("aria-disabled")).toBe("true");
  });

  it("calls onClick when enabled", () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
