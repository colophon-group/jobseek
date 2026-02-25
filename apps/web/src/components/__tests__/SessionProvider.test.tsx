import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SessionProvider, useSession } from "../SessionProvider";

function SessionDisplay() {
  const { user, isLoggedIn } = useSession();
  return (
    <div>
      <span data-testid="logged-in">{String(isLoggedIn)}</span>
      <span data-testid="user-name">{user?.name ?? "none"}</span>
    </div>
  );
}

describe("SessionProvider", () => {
  it("provides default context when no user", () => {
    render(
      <SessionProvider user={null}>
        <SessionDisplay />
      </SessionProvider>,
    );
    expect(screen.getByTestId("logged-in").textContent).toBe("false");
    expect(screen.getByTestId("user-name").textContent).toBe("none");
  });

  it("provides user context when user is set", () => {
    const user = {
      id: "1",
      email: "test@test.com",
      name: "Test User",
      emailVerified: true,
    };
    render(
      <SessionProvider user={user}>
        <SessionDisplay />
      </SessionProvider>,
    );
    expect(screen.getByTestId("logged-in").textContent).toBe("true");
    expect(screen.getByTestId("user-name").textContent).toBe("Test User");
  });
});

describe("useSession without provider", () => {
  it("returns default values", () => {
    render(<SessionDisplay />);
    expect(screen.getByTestId("logged-in").textContent).toBe("false");
    expect(screen.getByTestId("user-name").textContent).toBe("none");
  });
});
