import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  load: vi.fn(),
  logExternalError: vi.fn(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getUserWatchlistsWithLimit: (...args: unknown[]) => mocks.load(...args),
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: (...args: unknown[]) => mocks.logExternalError(...args),
}));

vi.mock("../watchlists-page", () => ({
  WatchlistsPage: ({
    initialWatchlists,
    limitReached,
    locale,
  }: {
    initialWatchlists: unknown[];
    limitReached: boolean;
    locale: string;
  }) => (
    <div
      data-testid="watchlists-page"
      data-count={initialWatchlists.length}
      data-limit-reached={String(limitReached)}
      data-locale={locale}
    />
  ),
}));

import { WatchlistsLoader } from "../watchlists-loader";

describe("WatchlistsLoader private overview", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads only overview metadata and passes it to the list page", async () => {
    mocks.load.mockResolvedValue({
      watchlists: [{ id: "watchlist-1" }, { id: "watchlist-2" }],
      limitReached: true,
    });

    render(await WatchlistsLoader({
      locale: "de",
      errorLabel: "We couldn't load your watchlists.",
      retryLabel: "Try again",
    }));

    const page = screen.getByTestId("watchlists-page");
    expect(mocks.load).toHaveBeenCalledWith("de");
    expect(page.getAttribute("data-count")).toBe("2");
    expect(page.getAttribute("data-limit-reached")).toBe("true");
    expect(page.getAttribute("data-locale")).toBe("de");
  });

  it("does not read selection cookies, sessions, owner details, or detail page data", () => {
    const source = readFileSync(
      "app/[lang]/(app)/watchlists/watchlists-loader.tsx",
      "utf8",
    );

    expect(source).not.toContain("next/headers");
    expect(source).not.toContain("getSession");
    expect(source).not.toContain("WATCHLIST_SELECTION_COOKIE");
    expect(source).not.toContain("getOwnedWatchlistById");
    expect(source).not.toContain("buildWatchlistPageData");
  });

  it("renders a private retry when the overview query fails", async () => {
    mocks.load.mockRejectedValue(new Error("database unavailable"));

    render(await WatchlistsLoader({
      locale: "it",
      errorLabel: "We couldn't load your watchlists.",
      retryLabel: "Try again",
    }));

    expect(screen.getByText("We couldn't load your watchlists.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try again" }).getAttribute("href"))
      .toBe("/it/watchlists");
    expect(mocks.logExternalError).toHaveBeenCalledOnce();
  });
});

describe("Watchlists route partial prerendering", () => {
  it("keeps the overview read behind Suspense with reduced-motion loading", () => {
    const source = readFileSync("app/[lang]/(app)/watchlists/page.tsx", "utf8");
    expect(source).toContain(
      "<Suspense fallback={<WatchlistsFallback label={loadingLabel} />}>",
    );
    expect(source).toContain("<WatchlistsLoader");
    expect(source).toContain("locale={locale}");
    expect(source).toContain("errorLabel={errorLabel}");
    expect(source).toContain("retryLabel={retryLabel}");
    expect(source).toContain('role="status"');
    expect(source).toContain("motion-safe:animate-spin");
  });
});
