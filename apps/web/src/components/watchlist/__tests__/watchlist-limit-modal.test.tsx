import { useRef, useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

import {
  WatchlistLimitModal,
  useWatchlistLimitModal,
} from "../watchlist-limit-modal";

function OpenModal() {
  const limitNotice = useWatchlistLimitModal();

  return (
    <>
      <button
        type="button"
        onClick={(event) => limitNotice.show(event.currentTarget)}
      >
        Open notice
      </button>
      <WatchlistLimitModal
        open={limitNotice.open}
        onOpenChange={limitNotice.setOpen}
        onCloseAutoFocus={limitNotice.restoreFocus}
      />
    </>
  );
}

function ModalWithUnavailableOpener({ mode }: { mode: "disabled" | "removed" }) {
  const limitNotice = useWatchlistLimitModal();
  const fallbackRef = useRef<HTMLButtonElement>(null);
  const [openerUnavailable, setOpenerUnavailable] = useState(false);

  return (
    <>
      {mode !== "removed" || !openerUnavailable ? (
        <button
          type="button"
          disabled={mode === "disabled" && openerUnavailable}
          onClick={(event) => {
            limitNotice.show(event.currentTarget, () => fallbackRef.current);
            setOpenerUnavailable(true);
          }}
        >
          Open notice
        </button>
      ) : null}
      <button ref={fallbackRef} type="button">Safe fallback</button>
      <WatchlistLimitModal
        open={limitNotice.open}
        onOpenChange={limitNotice.setOpen}
        onCloseAutoFocus={limitNotice.restoreFocus}
      />
    </>
  );
}

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
    render(
      <WatchlistLimitModal
        open
        onOpenChange={() => {}}
        onCloseAutoFocus={() => {}}
      />,
    );

    const notice = screen.getByRole("dialog", { name: "10-watchlist limit" });
    expect(notice.textContent).toContain(
      "You can have up to 10 watchlists. Delete one before adding another.",
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText(/upgrade/i)).toBeNull();
  });

  it("exposes dismiss and close controls", () => {
    const onOpenChange = vi.fn();
    render(
      <WatchlistLimitModal
        open
        onOpenChange={onOpenChange}
        onCloseAutoFocus={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("only animates its entrance when reduced motion is not requested", () => {
    render(
      <WatchlistLimitModal
        open
        onOpenChange={() => {}}
        onCloseAutoFocus={() => {}}
      />,
    );

    const notice = screen.getByRole("dialog", { name: "10-watchlist limit" });
    expectMotionSafeEntrance(notice);
    const classes = notice.className.split(/\s+/);
    expect(classes).toContain("motion-safe:data-[state=open]:zoom-in-95");
    expect(classes).not.toContain("data-[state=open]:zoom-in-95");
  });

  it.each([
    ["Got it", "button"],
    ["Close", "button"],
    ["Escape", "keyboard"],
    ["outside click", "outside"],
  ] as const)("restores focus to its opener after %s", async (_label, closeMethod) => {
    const user = userEvent.setup();
    render(<OpenModal />);

    const opener = screen.getByRole("button", { name: "Open notice" });
    await user.click(opener);
    await screen.findByRole("dialog", { name: "10-watchlist limit" });

    if (closeMethod === "button") {
      await user.click(screen.getByRole("button", { name: _label }));
    } else if (closeMethod === "keyboard") {
      await user.keyboard("{Escape}");
    } else {
      const overlay = [...document.querySelectorAll<HTMLElement>("[data-state='open']")]
        .find((element) => element.className.includes("bg-black/40"));
      expect(overlay).toBeTruthy();
      await user.click(overlay!);
    }

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(opener);
    });
  });

  it.each(["disabled", "removed"] as const)(
    "focuses the safe fallback when the opener is %s",
    async (mode) => {
      const user = userEvent.setup();
      render(<ModalWithUnavailableOpener mode={mode} />);

      await user.click(screen.getByRole("button", { name: "Open notice" }));
      await user.click(screen.getByRole("button", { name: "Got it" }));

      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(document.activeElement).toBe(
          screen.getByRole("button", { name: "Safe fallback" }),
        );
      });
    },
  );
});
