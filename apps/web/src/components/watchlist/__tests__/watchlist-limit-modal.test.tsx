import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

import { WatchlistLimitModal } from "../watchlist-limit-modal";

function expectMotionSafeEntrance(dialog: HTMLElement) {
  const overlay = [...document.querySelectorAll<HTMLElement>("[data-state='open']")]
    .find((element) => element !== dialog && element.className.includes("bg-black/40"));

  expect(overlay).toBeTruthy();
  for (const element of [overlay!, dialog]) {
    const classes = element.className.split(/\s+/);
    expect(classes).toContain("motion-safe:data-[state=open]:animate-in");
    expect(classes).toContain("motion-safe:data-[state=open]:fade-in-0");
    expect(classes).not.toContain("data-[state=open]:animate-in");
    expect(classes).not.toContain("data-[state=open]:fade-in-0");
  }
}

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

  it("only animates its entrance when reduced motion is not requested", () => {
    render(<WatchlistLimitModal open onOpenChange={() => {}} />);

    const notice = screen.getByRole("dialog", { name: "10-watchlist limit" });
    expectMotionSafeEntrance(notice);
    const classes = notice.className.split(/\s+/);
    expect(classes).toContain("motion-safe:data-[state=open]:zoom-in-95");
    expect(classes).not.toContain("data-[state=open]:zoom-in-95");
  });
});
