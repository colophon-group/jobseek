import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  load: vi.fn(),
  getOwned: vi.fn(),
  getSession: vi.fn(),
  cookieValue: undefined as string | undefined,
  decode: vi.fn(),
  build: vi.fn(),
  getPlan: vi.fn(),
  getPreferences: vi.fn(),
}));

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => mocks.cookieValue ? { value: mocks.cookieValue } : undefined,
  }),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getUserWatchlistsWithLimit: (...args: unknown[]) => mocks.load(...args),
  getOwnedWatchlistById: (...args: unknown[]) => mocks.getOwned(...args),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSession: () => mocks.getSession(),
}));

vi.mock("@/lib/watchlist-selection", () => ({
  WATCHLIST_SELECTION_COOKIE: "jobseek.watchlist-selection",
  decodeWatchlistSelection: (...args: unknown[]) => mocks.decode(...args),
}));

vi.mock("@/lib/services/watchlist-page-data", () => ({
  buildWatchlistPageData: (...args: unknown[]) => mocks.build(...args),
}));

vi.mock("@/lib/plans", () => ({
  getUserPlan: (...args: unknown[]) => mocks.getPlan(...args),
  PLAN_LIMITS: {
    free: { canReceiveAlerts: false },
    unlimited: { canReceiveAlerts: true },
  },
}));

vi.mock("@/lib/actions/preferences", () => ({
  getPreferences: (...args: unknown[]) => mocks.getPreferences(...args),
}));

vi.mock("../watchlists-page", () => ({
  WatchlistsPage: ({
    initialWatchlists,
    initialPageData,
    selectionSync,
    limitReached,
    locale,
  }: {
    initialWatchlists: unknown[];
    initialPageData: { detail?: { id?: string } } | null;
    selectionSync: string;
    limitReached: boolean;
    locale: string;
  }) => (
    <div
      data-testid="watchlists-page"
      data-count={initialWatchlists.length}
      data-active={initialPageData?.detail?.id ?? ""}
      data-selection-sync={selectionSync}
      data-limit-reached={String(limitReached)}
      data-locale={locale}
    />
  ),
}));

import { WatchlistsLoader } from "../watchlists-loader";

const FIRST_ID = "11111111-1111-4111-8111-111111111111";
const SECOND_ID = "22222222-2222-4222-8222-222222222222";
const overview = (id: string) => ({ id });
const detail = (id: string) => ({ id, filters: {}, companies: [] });

describe("WatchlistsLoader private selection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    for (const fn of Object.values(mocks)) {
      if (typeof fn === "function" && "mockReset" in fn) fn.mockReset();
    }
    mocks.cookieValue = undefined;
    mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.getPlan.mockResolvedValue("free");
    mocks.getPreferences.mockResolvedValue({ jobLanguages: ["en"] });
    mocks.build.mockImplementation(async ({ detail: active }) => ({ detail: active }));
  });

  it("uses a valid selected id only after the exact owner query succeeds", async () => {
    mocks.cookieValue = "signed";
    mocks.decode.mockReturnValue(SECOND_ID);
    mocks.load.mockResolvedValue({
      watchlists: [overview(FIRST_ID), overview(SECOND_ID)],
      limitReached: false,
    });
    mocks.getOwned.mockResolvedValue(detail(SECOND_ID));

    render(await WatchlistsLoader({ locale: "en" }));

    const page = screen.getByTestId("watchlists-page");
    expect(page.getAttribute("data-active")).toBe(SECOND_ID);
    expect(page.getAttribute("data-selection-sync")).toBe("none");
    expect(mocks.getOwned).toHaveBeenCalledWith(SECOND_ID, "user-1");
  });

  it("replaces a stale selection with the deterministic first owned row", async () => {
    mocks.cookieValue = "signed-stale";
    mocks.decode.mockReturnValue(SECOND_ID);
    mocks.load.mockResolvedValue({
      watchlists: [overview(FIRST_ID)],
      limitReached: false,
    });
    mocks.getOwned
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(detail(FIRST_ID));

    render(await WatchlistsLoader({ locale: "de" }));

    const page = screen.getByTestId("watchlists-page");
    expect(page.getAttribute("data-active")).toBe(FIRST_ID);
    expect(page.getAttribute("data-selection-sync")).toBe("replace");
    expect(mocks.getOwned.mock.calls).toEqual([
      [SECOND_ID, "user-1"],
      [FIRST_ID, "user-1"],
    ]);
  });

  it("never queries a cross-account hint decoded for another user", async () => {
    mocks.cookieValue = "bound-to-someone-else";
    mocks.decode.mockReturnValue(null);
    mocks.load.mockResolvedValue({
      watchlists: [overview(FIRST_ID)],
      limitReached: false,
    });
    mocks.getOwned.mockResolvedValue(detail(FIRST_ID));

    render(await WatchlistsLoader({ locale: "fr" }));

    expect(mocks.getOwned).toHaveBeenCalledOnce();
    expect(mocks.getOwned).toHaveBeenCalledWith(FIRST_ID, "user-1");
    expect(screen.getByTestId("watchlists-page").getAttribute("data-selection-sync"))
      .toBe("replace");
  });

  it("clears stale state when the owner has zero watchlists", async () => {
    mocks.cookieValue = "stale";
    mocks.decode.mockReturnValue(SECOND_ID);
    mocks.load.mockResolvedValue({ watchlists: [], limitReached: false });
    mocks.getOwned.mockResolvedValue(null);

    render(await WatchlistsLoader({ locale: "it" }));

    const page = screen.getByTestId("watchlists-page");
    expect(page.getAttribute("data-count")).toBe("0");
    expect(page.getAttribute("data-active")).toBe("");
    expect(page.getAttribute("data-selection-sync")).toBe("clear");
  });

  it("never serializes another account's data for an anonymous request", async () => {
    mocks.getSession.mockResolvedValue(null);
    mocks.cookieValue = "previous-account";
    mocks.load.mockResolvedValue({ watchlists: [], limitReached: true });

    render(await WatchlistsLoader({ locale: "en" }));

    expect(mocks.decode).not.toHaveBeenCalled();
    expect(mocks.getOwned).not.toHaveBeenCalled();
    expect(screen.getByTestId("watchlists-page").getAttribute("data-selection-sync"))
      .toBe("clear");
  });

  it("isolates consecutive users and locales without reusing private page data", async () => {
    mocks.cookieValue = "user-one-token";
    mocks.decode.mockReturnValue(FIRST_ID);
    mocks.load.mockResolvedValue({
      watchlists: [overview(FIRST_ID)],
      limitReached: false,
    });
    mocks.getOwned.mockResolvedValue(detail(FIRST_ID));

    const first = render(await WatchlistsLoader({ locale: "en" }));
    expect(screen.getByTestId("watchlists-page").getAttribute("data-active"))
      .toBe(FIRST_ID);
    first.unmount();

    mocks.getSession.mockResolvedValue({ user: { id: "user-2" } });
    mocks.cookieValue = "user-two-token";
    mocks.decode.mockReturnValue(SECOND_ID);
    mocks.load.mockResolvedValue({
      watchlists: [overview(SECOND_ID)],
      limitReached: false,
    });
    mocks.getOwned.mockResolvedValue(detail(SECOND_ID));

    render(await WatchlistsLoader({ locale: "it" }));
    const second = screen.getByTestId("watchlists-page");
    expect(second.getAttribute("data-active")).toBe(SECOND_ID);
    expect(second.getAttribute("data-locale")).toBe("it");
    expect(mocks.getOwned).toHaveBeenLastCalledWith(SECOND_ID, "user-2");
    expect(mocks.build).toHaveBeenLastCalledWith(
      expect.objectContaining({
        detail: expect.objectContaining({ id: SECOND_ID }),
        locale: "it",
      }),
    );
  });
});

describe("Watchlists route partial prerendering", () => {
  it("keeps session and cookie reads behind Suspense with reduced-motion loading", () => {
    const source = readFileSync("app/[lang]/(app)/watchlists/page.tsx", "utf8");
    expect(source).toContain("<Suspense fallback={<WatchlistsFallback />}>");
    expect(source).toContain("<WatchlistsLoader locale={locale} />");
    expect(source).toContain('role="status"');
    expect(source).toContain("motion-safe:animate-spin");
  });
});
