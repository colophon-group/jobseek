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
const sessionMock = vi.hoisted(() => ({ isLoggedIn: true, isPending: false }));

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
  useSession: () => ({
    user: sessionMock.isLoggedIn ? { username: "alice" } : null,
    isLoggedIn: sessionMock.isLoggedIn,
    isPending: sessionMock.isPending,
  }),
}));

vi.mock("@/lib/actions/watchlists", () => ({
  createWatchlist: (...args: unknown[]) => createWatchlistMock(...args),
}));

import { SaveSearchButton } from "../save-search-button";

describe("SaveSearchButton (issue #3036)", () => {
  beforeEach(() => {
    pushMock.mockReset();
    createWatchlistMock.mockReset();
    sessionMock.isLoggedIn = true;
    sessionMock.isPending = false;
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/en/explore?q=engineer&loc=switzerland");
  });

  it("does not stage an anonymous search or write an account while identity is unresolved", () => {
    sessionMock.isLoggedIn = false;
    sessionMock.isPending = true;
    render(<SaveSearchButton keywords={["engineer"]} locations={[]} occupations={[]} seniorities={[]} />);
    const button = screen.getByRole("button", { name: /save this search/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(window.sessionStorage.length).toBe(0);
    expect(createWatchlistMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("stages the filtered search without forcing an immediate sign-in", async () => {
    sessionMock.isLoggedIn = false;

    render(
      <SaveSearchButton
        keywords={["engineer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    expect(pushMock).toHaveBeenCalledWith(
      expect.stringMatching(/^\/en\/watchlists\/[0-9a-f-]+$/),
    );
    expect(createWatchlistMock).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(1);
  });

  it("explains a catalogue failure without changing the search route", async () => {
    createWatchlistMock.mockResolvedValue({ error: "company_lookup_unavailable" });
    render(<SaveSearchButton keywords={[]} locations={[]} occupations={[]} seniorities={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Company lookup is temporarily unavailable. Please try again.",
    );
    expect(pushMock).not.toHaveBeenCalled();
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

  it("bounds a generated title from valid long filters before saving", async () => {
    createWatchlistMock.mockResolvedValue({ id: "w1", slug: "long-search" });

    render(
      <SaveSearchButton
        keywords={[
          `${"x".repeat(99)}😀later`,
          ...Array.from({ length: 19 }, (_, index) => `keyword-${index}`),
        ]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/en/watchlists/w1"));
    const input = createWatchlistMock.mock.calls[0]?.[0] as { title: string };
    expect(input.title.length).toBeLessThanOrEqual(100);
    expect(input.title).not.toMatch(/[\uD800-\uDBFF]$/);
    expect((createWatchlistMock.mock.calls[0]?.[0] as {
      filters: { keywords: string[] };
    }).filters.keywords).toHaveLength(20);
  });

  it("includes employment type filters when saving the search", async () => {
    createWatchlistMock.mockResolvedValue({ id: "w1", slug: "contracts" });

    render(
      <SaveSearchButton
        keywords={["designer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
        employmentTypes={["contract"]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(createWatchlistMock).toHaveBeenCalledTimes(1));
    expect(createWatchlistMock.mock.calls[0]?.[0]).toMatchObject({
      filters: {
        keywords: ["designer"],
        employmentType: ["contract"],
      },
    });
  });

  it("creates a one-company watchlist from a company search", async () => {
    createWatchlistMock.mockResolvedValue({ id: "w1", slug: "acme" });

    render(
      <SaveSearchButton
        keywords={[]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
        companyScope={{ id: "company-1", name: "Acme" }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(createWatchlistMock).toHaveBeenCalledTimes(1));
    expect(createWatchlistMock.mock.calls[0]?.[0]).toMatchObject({
      title: "Acme",
      companyIds: ["company-1"],
      filters: { anyCompany: false },
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
