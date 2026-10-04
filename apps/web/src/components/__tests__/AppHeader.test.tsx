import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
import { SessionProvider } from "../providers/SessionProvider";
import { authClient } from "@/lib/auth-client";
import { AppHeader } from "../AppHeader";

vi.mock("@/lib/auth-client", () => ({ authClient: { signOut: vi.fn().mockResolvedValue({}) } }));
vi.mock("@/lib/useLocalePath", () => ({ useLocalePath: () => (path: string) => `/en${path}` }));
vi.mock("@/components/search/search-bar", () => ({ SearchBar: () => null }));
vi.mock("@/components/ThemedImage", () => ({ ThemedImage: () => null }));
vi.mock("@/components/UserAvatar", () => ({ UserAvatar: () => <span>Avatar</span> }));
vi.mock("next/link", () => ({
  default: ({ href, children, prefetch: _prefetch, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { prefetch?: boolean }) => <a href={href} {...props}>{children}</a>,
}));

describe("AppHeader account state", () => {
  it("offers a bounded manual retry without presenting login while identity is unavailable", () => {
    const retry = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<SessionProvider user={null} isPending accountStatus="unavailable" canRetry retry={retry}><AppHeader /></SessionProvider>);
    expect(screen.queryAllByRole("link", { name: "Log in" })).toHaveLength(0);
    const controls = screen.getAllByRole("button", { name: "Retry account" });
    expect(controls).toHaveLength(2);
    expect(controls.every((button) => button.title === "Account unavailable")).toBe(true);
    fireEvent.click(controls[0]);
    expect(retry).toHaveBeenCalledTimes(1);
    rerender(<SessionProvider user={null} isPending accountStatus="unavailable" isRefreshing retry={retry}><AppHeader /></SessionProvider>);
    expect(screen.getAllByRole("button", { name: "Retry account" }).every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "Retry account" })[1]);
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("shows login only after the anonymous path resolves", () => {
    const { rerender } = render(<SessionProvider user={null} isPending><AppHeader /></SessionProvider>);
    expect(screen.queryAllByRole("link", { name: "Log in" })).toHaveLength(0);
    rerender(<SessionProvider user={null}><AppHeader /></SessionProvider>);
    expect(screen.getAllByRole("link", { name: "Log in" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Retry account" })).toBeNull();
  });

  it("retains the account menu for a verified current identity", async () => {
    render(<SessionProvider user={{ id: "u1", name: "Alice", email: "alice@example.com", emailVerified: true }} accountStatus="unavailable" canRetry><AppHeader /></SessionProvider>);
    expect(screen.getByRole("button", { name: "Account menu" })).toBeTruthy();
    expect(screen.queryAllByRole("link", { name: "Log in" })).toHaveLength(0);
    await act(async () => { fireEvent.keyDown(screen.getByRole("button", { name: "Account menu" }), { key: "ArrowDown" }); });
    expect(screen.getByText("Account unavailable")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Retry account" })).toBeTruthy();
  });
});

it("invalidates the account before logout starts so pending responses cannot restore it", async () => {
  const calls: string[] = [];
  const invalidate = vi.fn(() => { calls.push("invalidate"); });
  vi.mocked(authClient.signOut).mockImplementationOnce(() => {
    calls.push("signOut");
    return new Promise(() => {});
  });
  render(<SessionProvider user={{ id: "u1", name: "Alice", email: "alice@example.com", emailVerified: true }} invalidate={invalidate}><AppHeader /></SessionProvider>);
  await act(async () => { fireEvent.keyDown(screen.getByRole("button", { name: "Account menu" }), { key: "ArrowDown" }); });
  fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(calls).toEqual(["invalidate", "signOut"]);
});
