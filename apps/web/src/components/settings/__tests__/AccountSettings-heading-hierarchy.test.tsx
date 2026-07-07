import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import "@/test-utils/lingui-mock";

/**
 * Regression test for #3196 — the settings page renders an `<h1>` in
 * the layout (`Settings`), and `AccountSettings` was jumping straight
 * to `<h3>` for every section, skipping h2 entirely. WCAG 1.3.1
 * forbids the gap; `GeneralSettings` already uses h2 correctly. This
 * suite locks the section level at h2 so a future refactor cannot
 * regress to h3.
 */

vi.mock("server-only", () => ({}));

// Settings page hands `useRouter` to AccountSettings via UsernameSection.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

// AccountSettings reads its viewer via useSession() — stub a logged-in
// user so the component renders its real sections rather than the
// LoginPrompt short-circuit.
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({
    user: { email: "test@example.com", username: "test" },
    isLoggedIn: true,
    isPending: false,
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/auth-client", () => ({
  authClient: {
    isUsernameAvailable: vi.fn().mockResolvedValue({ data: { available: true } }),
    requestPasswordReset: vi.fn().mockResolvedValue({ error: null }),
    changeEmail: vi.fn().mockResolvedValue({ error: null }),
    linkSocial: vi.fn().mockResolvedValue({ error: null }),
    unlinkAccount: vi.fn().mockResolvedValue({ error: null }),
    deleteUser: vi.fn().mockResolvedValue({ error: null }),
  },
}));

vi.mock("@/lib/actions/preferences", () => ({
  setPassword: vi.fn().mockResolvedValue({ error: null }),
  recordPasswordResetRequest: vi.fn().mockResolvedValue({ error: null }),
  getAccountPageData: vi.fn().mockResolvedValue(null),
  renameUsername: vi.fn().mockResolvedValue({ error: null }),
}));

import { AccountSettings } from "../AccountSettings";

const initialData = {
  accounts: [] as { providerId: string; accountId: string }[],
  hasPassword: false,
  username: "testuser",
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AccountSettings — section heading level (#3196)", () => {
  it("renders all section headings at level 2 (h2), not h3", () => {
    render(<AccountSettings initialData={{ ...initialData }} />);

    // Every section heading must be an h2 — the page-level h1 lives in
    // the settings layout, so h2 is the correct next step.
    const h2s = screen.getAllByRole("heading", { level: 2 });
    // Username, Password, Change email, Connected accounts, Delete
    // account = 5 sections. PasswordSection branches on `hasPassword`
    // and renders one of two sub-components, each with its own heading
    // — so there are 5 h2s, not 6, for any given render.
    expect(h2s.length).toBeGreaterThanOrEqual(5);
  });

  it("renders no h3 at the section level (WCAG 1.3.1 — no skipped heading levels)", () => {
    render(<AccountSettings initialData={{ ...initialData }} />);

    const h3s = screen.queryAllByRole("heading", { level: 3 });
    expect(h3s).toHaveLength(0);
  });
});
