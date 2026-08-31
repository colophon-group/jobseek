import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

import { WatchlistLimitModal } from "../watchlist-limit-modal";

describe("WatchlistLimitModal", () => {
  it("explains the universal cap without a billing or upgrade action", () => {
    render(<WatchlistLimitModal open onOpenChange={() => {}} />);

    const notice = screen.getByRole("dialog", { name: "10-watchlist limit" });
    expect(notice.textContent).toContain(
      "You can have up to 10 watchlists. Delete one before adding another.",
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText(/upgrade/i)).toBeNull();
  });

  it("exposes dismiss and close controls", () => {
    const onOpenChange = vi.fn();
    render(<WatchlistLimitModal open onOpenChange={onOpenChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
