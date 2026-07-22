import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  load: vi.fn(),
  getSession: vi.fn(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getUserWatchlistsWithLimit: (...args: unknown[]) => mocks.load(...args),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSession: () => mocks.getSession(),
}));

vi.mock("../watchlists-page", () => ({
  WatchlistsPage: ({
    initialWatchlists,
    username,
    limitReached,
    locale,
  }: {
    initialWatchlists: unknown[];
    username: string | null;
    limitReached: boolean;
    locale: string;
  }) => (
    <div
      data-testid="watchlists-page"
      data-count={initialWatchlists.length}
      data-username={username ?? ""}
      data-limit-reached={String(limitReached)}
      data-locale={locale}
    />
  ),
}));

import { WatchlistsLoader } from "../watchlists-loader";

describe("WatchlistsLoader server read (#5896)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.load.mockReset();
    mocks.getSession.mockReset();
    mocks.getSession.mockResolvedValue({ user: { username: "alice" } });
  });

  it("passes the server-loaded overview to the interactive page", async () => {
    mocks.load.mockResolvedValue({
      watchlists: [{ id: "wl-1" }],
      limitReached: false,
    });

    render(await WatchlistsLoader({ locale: "en" }));

    const page = screen.getByTestId("watchlists-page");
    expect(page.getAttribute("data-count")).toBe("1");
    expect(page.getAttribute("data-username")).toBe("alice");
    expect(page.getAttribute("data-limit-reached")).toBe("false");
    expect(page.getAttribute("data-locale")).toBe("en");
    expect(mocks.load).toHaveBeenCalledOnce();
    expect(mocks.load).toHaveBeenCalledWith("en");
  });

  it("renders the anonymous overview without a username", async () => {
    mocks.getSession.mockResolvedValue(null);
    mocks.load.mockResolvedValue({ watchlists: [], limitReached: true });

    render(await WatchlistsLoader({ locale: "de" }));

    const page = screen.getByTestId("watchlists-page");
    expect(page.getAttribute("data-username")).toBe("");
    expect(page.getAttribute("data-limit-reached")).toBe("true");
  });

  it("renders a localized hard-reload recovery link when the server read fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.load.mockRejectedValue(new Error("database unavailable"));

    render(await WatchlistsLoader({ locale: "fr" }));

    expect(screen.getByText(/couldn't load your watchlists/i)).toBeTruthy();
    expect(screen.getByRole("link", { name: /try again/i }).getAttribute("href")).toBe(
      "/fr/watchlists",
    );
    expect(mocks.load).toHaveBeenCalledOnce();
  });
});

describe("Watchlists route partial prerendering", () => {
  it("places the session-scoped server loader behind Suspense", () => {
    const source = readFileSync(
      "app/[lang]/(app)/watchlists/page.tsx",
      "utf8",
    );

    expect(source).toContain("<Suspense fallback={<WatchlistsFallback />}>");
    expect(source).toContain("<WatchlistsLoader locale={locale} />");
  });
});
