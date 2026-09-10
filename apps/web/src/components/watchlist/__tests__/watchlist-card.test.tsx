/**
 * Tests for the watchlist CreateWatchlistCard disabled state — issue
 * #3036 sub-bug 2. The card must:
 *   1. dim visually (`opacity-50`) when `disabled`
 *   2. not invoke `onClick` when `disabled` (so it can't create a 2nd
 *      watchlist on a free plan)
 *   3. expose the account-wide limit in its accessible name and show the
 *      same explanation when a touch user taps it
 */
import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { prefetch?: boolean }) => (
    <a href={href} data-prefetch={String(prefetch)} {...props}>{children}</a>
  ),
}));

vi.mock("next/image", () => ({
  default: ({ src, alt }: { src: string; alt: string }) => (
    <span
      role={alt ? "img" : "presentation"}
      aria-label={alt || undefined}
      data-src={src}
    />
  ),
}));

import { CreateWatchlistCard, WatchlistCard } from "../watchlist-card";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("WatchlistCard overview link", () => {
  function actionableCard(overrides: {
    onShare?: () => Promise<void>;
    onDelete?: () => Promise<void>;
  } = {}) {
    return (
      <WatchlistCard
        href="/en/watchlists/watchlist-1"
        activity={{ activeCompanyCount: 4, activeJobCount: 34, topCompanies: [] }}
        onShare={overrides.onShare ?? vi.fn().mockResolvedValue(undefined)}
        onDelete={overrides.onDelete ?? vi.fn().mockResolvedValue(undefined)}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: null,
          isShared: false,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: 34,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />
    );
  }

  it("links directly to the owner-only detail route", () => {
    render(
      <WatchlistCard
        href="/en/watchlists/watchlist-1"
        activity={{ activeCompanyCount: 12, activeJobCount: 34, topCompanies: [] }}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: null,
          isShared: false,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: 34,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    const link = screen.getByRole("link", { name: /maang\+/i });
    expect(link.getAttribute("href")).toBe("/en/watchlists/watchlist-1");
    expect(link.getAttribute("data-prefetch")).toBe("false");
    expect(link.className).toContain("w-full");
  });

  it("shows a two-line description and exact active company and job counts", () => {
    render(
      <WatchlistCard
        href="/en/watchlists/watchlist-1"
        activity={{ activeCompanyCount: 4, activeJobCount: 34, topCompanies: [] }}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: "Engineering roles at a focused group of companies.",
          isShared: false,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: 34,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    const description = screen.getByText(
      "Engineering roles at a focused group of companies.",
    );
    expect(description.className).toContain("line-clamp-2");
    expect(screen.getByText("4 companies")).toBeTruthy();
    expect(screen.getByText("34 jobs")).toBeTruthy();
    expect(screen.queryByText("12 companies")).toBeNull();
    expect(screen.queryByText("All companies")).toBeNull();
  });

  it("keeps activity metrics in a non-verbal skeleton while they load", () => {
    render(
      <WatchlistCard
        href="/en/watchlists/watchlist-1"
        activity={null}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: null,
          isShared: false,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: null,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    expect(screen.queryByText("12 companies")).toBeNull();
    expect(screen.queryByText(/jobs?$/)).toBeNull();
    expect(screen.queryByText("All companies")).toBeNull();
  });

  it("shows unavailable metrics without a loading animation after the request settles", () => {
    render(
      <WatchlistCard
        href="/en/watchlists/watchlist-1"
        activity={null}
        activityPending={false}
        watchlist={{
          id: "watchlist-1",
          slug: "maangplus",
          title: "MAANG+",
          description: null,
          isShared: false,
          alertsEnabled: false,
          companyCount: 12,
          activeJobCount: null,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    expect(screen.getByText("— companies")).toBeTruthy();
    expect(screen.getByText("— jobs")).toBeTruthy();
    expect(document.querySelector(".animate-pulse")).toBeNull();
  });

  it("renders the logo stack in server-ranked order with one accessible group label", () => {
    render(
      <WatchlistCard
        href="/en/watchlists/watchlist-1"
        activity={{
          activeCompanyCount: 3,
          activeJobCount: 125,
          topCompanies: [
            { id: "company-beta", name: "Beta", icon: "https://example.com/beta.webp" },
            { id: "company-acme", name: "Acme", icon: "https://example.com/acme.webp" },
            { id: "company-gamma", name: "Gamma", icon: null },
          ],
        }}
        watchlist={{
          id: "watchlist-1",
          slug: "all-engineering",
          title: "All engineering",
          description: null,
          isShared: false,
          alertsEnabled: false,
          companyCount: 0,
          activeJobCount: 125,
          lastAccessedAt: "2026-07-06T00:00:00.000Z",
          createdAt: "2026-07-06T00:00:00.000Z",
        }}
      />,
    );

    const stack = screen.getByLabelText("Beta, Acme, Gamma");
    expect(Array.from(stack.children).map((child) => child.getAttribute("title")))
      .toEqual(["Beta", "Acme", "Gamma"]);
    expect(within(stack).getAllByRole("presentation")).toHaveLength(2);
    expect(stack.children[2]?.querySelector("svg[aria-hidden='true']")).not.toBeNull();
    expect(stack.className).toContain("-space-x-2");
    expect(stack.className).toContain("h-7");
    expect(screen.getByText("3 companies")).toBeTruthy();
    expect(screen.getByText("125 jobs")).toBeTruthy();
    expect(screen.queryByText("All companies")).toBeNull();
  });

  it("keeps navigation and desktop/mobile action controls as sibling interactives", () => {
    render(actionableCard());

    const link = screen.getByRole("link", { name: /MAANG\+/ });
    expect(link.querySelector("button")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Share" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
    const mobileActions = document.getElementById("watchlist-actions-watchlist-1");
    const mobileShare = within(mobileActions!).getByRole("button", { name: "Share" });
    const surface = screen.getByTestId("watchlist-card-drag-surface");
    expect(mobileActions?.hasAttribute("aria-hidden")).toBe(false);
    expect(mobileActions?.className).toContain("opacity-0");
    expect(mobileActions?.className).toContain("pointer-events-none");
    expect(mobileShare.tabIndex).toBe(0);
    expect(link.compareDocumentPosition(mobileShare) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();

    fireEvent.focus(mobileShare);
    expect(mobileActions?.className).toContain("opacity-100");
    expect(surface.getAttribute("style")).toContain("translate3d(112px, 0, 0)");
    expect(screen.queryByRole("button", { name: "Watchlist actions" })).toBeNull();
  });

  it("reveals compact, outline-layered mobile actions after a rightward swipe", async () => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    Element.prototype.setPointerCapture = vi.fn();
    Element.prototype.hasPointerCapture = vi.fn(() => true);
    Element.prototype.releasePointerCapture = vi.fn();
    render(actionableCard());

    const surface = screen.getByTestId("watchlist-card-drag-surface");
    fireEvent.pointerDown(surface, {
      pointerId: 1,
      pointerType: "touch",
      clientX: 12,
      clientY: 12,
    });
    fireEvent.pointerMove(surface, {
      pointerId: 1,
      pointerType: "touch",
      clientX: 96,
      clientY: 14,
    });
    fireEvent.pointerUp(surface, {
      pointerId: 1,
      pointerType: "touch",
      clientX: 96,
      clientY: 14,
    });

    const mobileActions = document.getElementById("watchlist-actions-watchlist-1")!;
    const share = within(mobileActions).getByRole("button", { name: "Share" });
    const remove = within(mobileActions).getByRole("button", { name: "Delete" });
    expect(mobileActions.hasAttribute("aria-hidden")).toBe(false);
    expect(mobileActions.className).toContain("opacity-100");
    expect(share.tabIndex).toBe(0);
    expect(share.className).toContain("w-14");
    expect(share.className).toContain("rounded-l-xl");
    expect(remove.className).toContain("rounded-l-xl");
    expect(remove.className).not.toContain("rounded-xl");
    expect(remove.className).toContain("w-14");
    expect(remove.className).not.toContain("-ml-");
    const shareUnderlay = screen.getByTestId("watchlist-share-action-underlay");
    expect(shareUnderlay.className).toContain("w-[4.375rem]");
    expect(shareUnderlay.className).toContain("border-y");
    expect(mobileActions.className).toContain("w-[7.875rem]");
    expect(mobileActions.className).not.toContain("border-y");
    expect(screen.getByTestId("watchlist-delete-action-edge").className)
      .toContain("border-y");
    expect(surface.className).toContain("z-20");
    expect(surface.getAttribute("style")).toContain("translate3d(112px, 0, 0)");

    share.focus();
    fireEvent.keyDown(share, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(
      screen.getByRole("link", { name: /MAANG\+/ }),
    ));
    expect(mobileActions.className).toContain("opacity-0");
    vi.unstubAllGlobals();
  });

  it("announces a copied share link only after the share callback succeeds", async () => {
    let resolveShare: (() => void) | undefined;
    const onShare = vi.fn(() => new Promise<void>((resolve) => {
      resolveShare = resolve;
    }));
    render(actionableCard({ onShare }));

    const desktopShare = screen.getAllByRole("button", { name: "Share" })
      .find((button) => button.className.includes("size-8"))!;
    fireEvent.click(desktopShare);
    expect(onShare).toHaveBeenCalledOnce();
    expect(screen.queryByRole("status")).toBeNull();
    resolveShare?.();

    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Link copied");
    expect(status.parentElement?.className).toContain("bg-tooltip-bg");
    expect(status.parentElement?.getAttribute("data-side")).toBe("top");
    expect(screen.getAllByRole("button", { name: "Link copied" })
      .find((button) => button.className.includes("size-8"))?.className)
      .toContain("cursor-pointer");
  });

  it("anchors mobile share feedback to the revealed mobile action only", async () => {
    let resolveShare: (() => void) | undefined;
    const onShare = vi.fn(() => new Promise<void>((resolve) => {
      resolveShare = resolve;
    }));
    render(actionableCard({ onShare }));

    const mobileActions = document.getElementById("watchlist-actions-watchlist-1")!;
    const mobileShare = within(mobileActions).getByRole("button", { name: "Share" });
    mobileShare.focus();
    expect(document.activeElement).toBe(mobileShare);
    fireEvent.click(mobileShare);

    expect(document.activeElement).toBe(mobileShare);
    expect(mobileShare.hasAttribute("disabled")).toBe(false);
    expect(mobileShare.getAttribute("aria-disabled")).toBe("true");
    expect(mobileShare.getAttribute("aria-busy")).toBe("true");
    expect(mobileActions.className).toContain("opacity-100");
    fireEvent.click(mobileShare);
    expect(onShare).toHaveBeenCalledOnce();
    resolveShare?.();

    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Link copied");
    expect(status.parentElement?.className).toContain("md:hidden");
    expect(screen.getAllByRole("tooltip")).toHaveLength(1);
    expect(mobileShare.getAttribute("aria-disabled")).toBe("false");
  });

  it("snaps back after lost pointer capture instead of leaving a partial swipe", () => {
    Element.prototype.setPointerCapture = vi.fn();
    Element.prototype.hasPointerCapture = vi.fn(() => true);
    Element.prototype.releasePointerCapture = vi.fn();
    render(actionableCard());
    const surface = screen.getByTestId("watchlist-card-drag-surface");

    fireEvent.pointerDown(surface, { pointerId: 1, pointerType: "touch", clientX: 10, clientY: 10 });
    fireEvent.pointerMove(surface, { pointerId: 1, pointerType: "touch", clientX: 70, clientY: 12 });
    fireEvent.lostPointerCapture(surface, { pointerId: 1, pointerType: "touch" });

    expect(surface.getAttribute("style")).toContain("translate3d(0px, 0, 0)");
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

  it("does not call onClick when disabled and explains the limit on tap", async () => {
    const onClick = vi.fn();
    render(<CreateWatchlistCard onClick={onClick} disabled />);
    const btn = screen.getByRole("button", { name: /maximum of 10/i });
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
    const explanation = await screen.findByRole("tooltip");
    expect(explanation.textContent).toContain("Maximum of 10 watchlists reached");
    expect(explanation.className).toContain("bg-warning-bg");
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
    expect(btn.className).toContain("w-full");
    expect(btn.className).toContain("border-dashed");
  });
});
