/** Tests for the watchlist CreateWatchlistCard limit state. */
import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => `/en${p}`,
}));

import { CreateWatchlistCard, WatchlistCard } from "../watchlist-card";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("WatchlistCard navigation", () => {
  it("scrolls to top synchronously when navigating to a watchlist", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

    render(
      <WatchlistCard
        ownerUsername="colophongroup"
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

    fireEvent.click(screen.getByRole("link", { name: /maang\+/i }));

    expect(scrollTo).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "instant" });
  });

  it("shows a useful company count until the live job count arrives", () => {
    render(
      <WatchlistCard
        ownerUsername="colophongroup"
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
  it("applies dimmed styling when the universal limit is reached", () => {
    render(<CreateWatchlistCard onClick={() => {}} onLimitReached={() => {}} limitReached />);
    // The button is the Tooltip trigger when disabled; find by accessible
    // text "Create".
    const btn = screen.getByRole("button", { name: /create/i });
    expect(btn.className).toContain("opacity-50");
  });

  it("does not call the create callback when the limit is reached", () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} onLimitReached={() => {}} limitReached />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("delegates the reached-limit notice to the overview", () => {
    const onLimitReached = vi.fn();
    render(<CreateWatchlistCard onClick={() => {}} onLimitReached={onLimitReached} limitReached />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);
    expect(onLimitReached).toHaveBeenCalledOnce();
  });

  it("calls onClick when enabled", () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} onLimitReached={() => {}} />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
