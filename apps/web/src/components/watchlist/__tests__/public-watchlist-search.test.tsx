import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ lang: "en" }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => `/en${p}`,
}));

vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: () => ({ sentinelRef: { current: null }, isLoading: false }),
}));

vi.mock("@/lib/actions/watchlists", () => ({
  getPopularWatchlists: vi.fn(),
  searchPublicWatchlists: vi.fn(),
}));

import { getPopularWatchlists, searchPublicWatchlists } from "@/lib/actions/watchlists";
import { PublicWatchlistSearch } from "../public-watchlist-search";

const getPopularWatchlistsMock = vi.mocked(getPopularWatchlists);
const searchPublicWatchlistsMock = vi.mocked(searchPublicWatchlists);

beforeEach(() => {
  vi.restoreAllMocks();
  getPopularWatchlistsMock.mockResolvedValue({
    watchlists: [
      {
        id: "public-watchlist-1",
        slug: "maangplus",
        title: "MAANG+",
        description: "Big tech and AI jobs",
        isPublic: true,
        alertsEnabled: false,
        companyCount: 12,
        activeJobCount: 45,
        lastAccessedAt: "2026-07-06T00:00:00.000Z",
        createdAt: "2026-07-06T00:00:00.000Z",
        ownerName: "Colophon",
        ownerUsername: "colophongroup",
        mirrorCount: 2,
      },
    ],
    total: 1,
  });
  searchPublicWatchlistsMock.mockResolvedValue({ watchlists: [], total: 0 });
});

describe("PublicWatchlistSearch navigation", () => {
  it("scrolls to top synchronously when opening a public watchlist result", async () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

    render(<PublicWatchlistSearch />);

    const link = await screen.findByRole("link", { name: /maang\+/i });
    fireEvent.click(link);

    expect(link.getAttribute("href")).toBe("/en/colophongroup/maangplus");
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "instant" });
  });
});
