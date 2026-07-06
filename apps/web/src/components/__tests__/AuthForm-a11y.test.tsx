import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  signInEmail: vi.fn(),
  signInUsername: vi.fn(),
  signUpEmail: vi.fn(),
  getPreferences: vi.fn(),
  updatePreferences: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/auth-client", () => ({
  authClient: {
    signIn: {
      email: mocks.signInEmail,
      username: mocks.signInUsername,
      social: vi.fn(),
    },
    signUp: {
      email: mocks.signUpEmail,
    },
  },
}));

vi.mock("@/lib/actions/preferences", () => ({
  getPreferences: mocks.getPreferences,
  updatePreferences: mocks.updatePreferences,
}));

vi.mock("@/lib/preference-timestamps", () => ({
  localPrefs: {
    themeTimestamp: { get: vi.fn(), set: vi.fn() },
    localeTimestamp: { get: vi.fn(), set: vi.fn() },
    locale: { get: vi.fn(), set: vi.fn() },
  },
}));

import { AuthForm } from "../AuthForm";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AuthForm accessibility", () => {
  it("focuses the error banner and marks missing fields invalid", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="sign-in" />);

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    await waitFor(() => {
      expect(document.activeElement).toBe(alert);
    });

    const email = screen.getByLabelText("Email or username") as HTMLInputElement;
    const password = screen.getByLabelText("Password", { selector: "input" }) as HTMLInputElement;

    expect(alert.textContent).toBe("Please fill in all fields");
    expect(email.getAttribute("aria-invalid")).toBe("true");
    expect(password.getAttribute("aria-invalid")).toBe("true");
    expect(email.getAttribute("aria-describedby")).toBeTruthy();
    expect(password.getAttribute("aria-describedby")).toBeTruthy();
    expect(mocks.signInEmail).not.toHaveBeenCalled();
    expect(mocks.signInUsername).not.toHaveBeenCalled();
  });
});
