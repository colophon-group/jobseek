/**
 * Tests for SaveSearchButton — issue #3036 sub-bug 1.
 *
 * When createWatchlist returns `{ error: "limit_reached" }`, the
 * limit is explained in place without routing the user to a billing page
 * that has no available purchase action.
 */
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

  it("shows a non-purchase limit explanation when the server reports limit_reached", async () => {
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

    expect(await screen.findByText("Maximum of 10 watchlists reached")).toBeTruthy();
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

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/en/watchlists/w1"));
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

  it("preserves the current selection when creation fails", async () => {
    createWatchlistMock.mockRejectedValue(new Error("database unavailable"));

    render(
      <SaveSearchButton
        keywords={["engineer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(createWatchlistMock).toHaveBeenCalledOnce());
    expect(pushMock).not.toHaveBeenCalled();
  });
});
