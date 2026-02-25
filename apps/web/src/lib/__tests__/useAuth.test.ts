import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

const mockUseSession = vi.fn();
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => mockUseSession(),
}));

import { useAuth } from "../useAuth";

describe("useAuth", () => {
  it("returns logged in state when user exists", () => {
    const user = { id: "1", email: "a@b.com", name: "A", emailVerified: true };
    mockUseSession.mockReturnValue({ user, isLoggedIn: true });

    const { result } = renderHook(() => useAuth());
    expect(result.current.isLoggedIn).toBe(true);
    expect(result.current.user).toEqual(user);
    expect(result.current.isPending).toBe(false);
  });

  it("returns logged out state when no user", () => {
    mockUseSession.mockReturnValue({ user: null, isLoggedIn: false });

    const { result } = renderHook(() => useAuth());
    expect(result.current.isLoggedIn).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.isPending).toBe(false);
  });
});
