import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  routerRefresh: vi.fn(),
  sessionRefresh: vi.fn(),
  isUsernameAvailable: vi.fn(),
  requestPasswordReset: vi.fn(),
  changeEmail: vi.fn(),
  linkSocial: vi.fn(),
  unlinkAccount: vi.fn(),
  deleteUser: vi.fn(),
  setPassword: vi.fn(),
  recordPasswordResetRequest: vi.fn(),
  getAccountPageData: vi.fn(),
  renameUsername: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: mocks.routerRefresh,
  }),
}));

vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    user: { email: "test@example.com", username: "testuser" },
    isLoggedIn: true,
    isPending: false,
    refresh: mocks.sessionRefresh,
  }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/auth-client", () => ({
  authClient: {
    isUsernameAvailable: mocks.isUsernameAvailable,
    requestPasswordReset: mocks.requestPasswordReset,
    changeEmail: mocks.changeEmail,
    linkSocial: mocks.linkSocial,
    unlinkAccount: mocks.unlinkAccount,
    deleteUser: mocks.deleteUser,
  },
}));

vi.mock("@/lib/actions/preferences", () => ({
  setPassword: mocks.setPassword,
  recordPasswordResetRequest: mocks.recordPasswordResetRequest,
  getAccountPageData: mocks.getAccountPageData,
  renameUsername: mocks.renameUsername,
}));

import { AccountSettings } from "../AccountSettings";

const initialData = {
  accounts: [] as { providerId: string; accountId: string }[],
  hasPassword: false,
  username: "testuser",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.isUsernameAvailable.mockResolvedValue({ data: { available: true } });
  mocks.requestPasswordReset.mockResolvedValue({ error: null });
  mocks.changeEmail.mockResolvedValue({ error: null });
  mocks.linkSocial.mockResolvedValue({ error: null });
  mocks.unlinkAccount.mockResolvedValue({ error: null });
  mocks.deleteUser.mockResolvedValue({ error: null });
  mocks.setPassword.mockResolvedValue({ error: null });
  mocks.recordPasswordResetRequest.mockResolvedValue({ error: null });
  mocks.getAccountPageData.mockResolvedValue(null);
  mocks.renameUsername.mockResolvedValue({ error: null });
});

async function submitUsername(value: string) {
  const user = userEvent.setup();
  const username = screen.getByLabelText("Username");
  await user.clear(username);
  await user.type(username, value);

  await waitFor(() => {
    expect(mocks.isUsernameAvailable).toHaveBeenCalledWith({ username: value });
  });

  const submit = screen.getByRole("button", { name: "Update username" }) as HTMLButtonElement;
  await waitFor(() => {
    expect(submit.disabled).toBe(false);
  });
  await user.click(submit);
}

describe("AccountSettings username errors", () => {
  it("renders the renameUsername error and does not refresh stale session data", async () => {
    mocks.renameUsername.mockResolvedValueOnce({ error: "Username already taken" });

    render(<AccountSettings initialData={{ ...initialData }} />);
    await submitUsername("newname");

    await waitFor(() => {
      expect(mocks.renameUsername).toHaveBeenCalledWith("newname");
    });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Username already taken");
    expect(mocks.sessionRefresh).not.toHaveBeenCalled();
    expect(mocks.routerRefresh).not.toHaveBeenCalled();
  });

  it("renders the generic username error when renameUsername throws", async () => {
    mocks.renameUsername.mockRejectedValueOnce(new Error("network unavailable"));

    render(<AccountSettings initialData={{ ...initialData }} />);
    await submitUsername("othername");

    await waitFor(() => {
      expect(mocks.renameUsername).toHaveBeenCalledWith("othername");
    });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Failed to update username");
    expect(mocks.sessionRefresh).not.toHaveBeenCalled();
    expect(mocks.routerRefresh).not.toHaveBeenCalled();
  });
});
