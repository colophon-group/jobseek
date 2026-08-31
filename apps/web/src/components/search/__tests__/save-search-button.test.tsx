/**
 * Tests for SaveSearchButton — issue #3036 sub-bug 1.
 *
 * When createWatchlist returns `{ error: "limit_reached" }`, the
 * pre-fix behavior was a silent `router.push("/settings")` (opaque
 * redirect to the General tab, no reason shown). Post-fix the same
 * upgrade modal used elsewhere in the gating subsystem opens; its CTA
 * links to `/settings/billing` (locked down by upgrade-modal.test.tsx).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const pushMock = vi.fn();
const createWatchlistMock = vi.fn();
const selectOwnedWatchlistMock = vi.fn();
const broadcastSelectionMock = vi.fn();

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

vi.mock("@/lib/actions/watchlist-selection", () => ({
  selectOwnedWatchlist: (watchlistId: string) =>
    selectOwnedWatchlistMock(watchlistId),
}));

vi.mock("@/lib/watchlist-selection-client", () => ({
  broadcastWatchlistSelectionChanged: () => broadcastSelectionMock(),
}));

import { SaveSearchButton } from "../save-search-button";

describe("SaveSearchButton (issue #3036)", () => {
  beforeEach(() => {
    pushMock.mockReset();
    createWatchlistMock.mockReset();
    selectOwnedWatchlistMock.mockReset().mockResolvedValue({ ok: true });
    broadcastSelectionMock.mockReset();
  });

  it("opens upgrade modal (not a redirect) when the server reports limit_reached", async () => {
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

    // Upgrade CTA appears (linking to /settings/billing — sub-bug 3) and
    // there is NO opaque router.push to /settings (sub-bug 1).
    const upgradeLink = await screen.findByRole("link", { name: /upgrade/i });
    expect(upgradeLink.getAttribute("href")).toBe("/en/settings/billing");
    await waitFor(() => expect(createWatchlistMock).toHaveBeenCalledTimes(1));
    expect(pushMock).not.toHaveBeenCalledWith("/en/settings");
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

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/en/watchlists"));
    expect(selectOwnedWatchlistMock).toHaveBeenCalledWith("w1");
    expect(broadcastSelectionMock).toHaveBeenCalledOnce();
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
    expect(selectOwnedWatchlistMock).not.toHaveBeenCalled();
    expect(broadcastSelectionMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not navigate or broadcast when destination selection is rejected", async () => {
    createWatchlistMock.mockResolvedValue({ id: "w1", slug: "my-search" });
    selectOwnedWatchlistMock.mockResolvedValue({ ok: false });

    render(
      <SaveSearchButton
        keywords={["engineer"]}
        locations={[]}
        occupations={[]}
        seniorities={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /save this search/i }));

    await waitFor(() => expect(selectOwnedWatchlistMock).toHaveBeenCalledWith("w1"));
    expect(broadcastSelectionMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
