/**
 * Tests for the watchlist CreateWatchlistCard disabled state — issue
 * #3036 sub-bug 2. The card must:
 *   1. dim visually (`opacity-50`) when `disabled`
 *   2. not invoke `onClick` when `disabled` (so it can't create a 2nd
 *      watchlist on a free plan)
 *   3. open the upgrade modal instead, telling the user why nothing
 *      happened
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => `/en${p}`,
}));

import { CreateWatchlistCard } from "../watchlist-card";

describe("CreateWatchlistCard (issue #3036)", () => {
  it("applies dimmed styling when disabled", () => {
    render(<CreateWatchlistCard onClick={() => {}} disabled />);
    // The button is the Tooltip trigger when disabled; find by accessible
    // text "Create".
    const btn = screen.getByRole("button", { name: /create/i });
    expect(btn.className).toContain("opacity-50");
  });

  it("does not call onClick when disabled (gating intact)", () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} disabled />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("opens the upgrade modal (with billing CTA) when clicked while disabled", async () => {
    render(<CreateWatchlistCard onClick={() => {}} disabled />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);

    // The upgrade modal portals a link to /settings/billing — sub-bug 3.
    const link = await screen.findByRole("link", { name: /upgrade/i });
    expect(link.getAttribute("href")).toBe("/en/settings/billing");
  });

  it("calls onClick when enabled", () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} />);
    const btn = screen.getByRole("button", { name: /create/i });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
