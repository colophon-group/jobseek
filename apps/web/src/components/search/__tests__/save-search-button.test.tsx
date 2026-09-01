/** Tests for saving a search through the universal watchlist-cap flow. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const pushMock = vi.fn();
const createWatchlistMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => `/en${p}`,
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({ user: { username: "alice" }, isLoggedIn: true }),
}));

vi.mock("@/lib/actions/watchlists", () => ({
  createWatchlist: (...args: unknown[]) => createWatchlistMock(...args),
}));

import { SaveSearchButton } from "../save-search-button";

describe("SaveSearchButton (issue #3036)", () => {
  beforeEach(() => {
    pushMock.mockReset();
    createWatchlistMock.mockReset();
  });

  it("opens a neutral limit notice when the server reports limit_reached", async () => {
    createWatchlistMock.mockResolvedValue({ error: "limit_reached" });

    render(
      <SaveSearchButton
        keywords={["engineer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    const notice = await screen.findByRole("dialog", { name: "10-watchlist limit" });
    expect(notice.textContent).toContain(
      "You can have up to 10 watchlists. Delete one before adding another.",
    );
    expect(screen.queryByRole("link", { name: /upgrade/i })).toBeNull();
    await waitFor(() => expect(createWatchlistMock).toHaveBeenCalledTimes(1));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("navigates to the new watchlist on success", async () => {
    createWatchlistMock.mockResolvedValue({ id: "w1", slug: "my-search" });

    render(
      <SaveSearchButton
        keywords={["engineer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/en/alice/my-search"));
    expect(createWatchlistMock.mock.calls[0]?.[0]).toMatchObject({
      isPublic: false,
    });
  });

  it("includes employment type filters when saving the search", async () => {
    createWatchlistMock.mockResolvedValue({ id: "w1", slug: "internships" });

    render(
      <SaveSearchButton
        keywords={["designer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
        employmentTypes={["internship"]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(createWatchlistMock).toHaveBeenCalledTimes(1));
    expect(createWatchlistMock.mock.calls[0]?.[0]).toMatchObject({
      filters: {
        keywords: ["designer"],
        employmentType: ["internship"],
      },
    });
  });
});
