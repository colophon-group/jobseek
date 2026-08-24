import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  linkSocial: vi.fn(),
  unlinkAccount: vi.fn(),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/auth-client", () => ({
  authClient: {
    linkSocial: mocks.linkSocial,
    unlinkAccount: mocks.unlinkAccount,
  },
}));

import { ConnectedAccountsSection } from "../ConnectedAccountsSection";

describe("ConnectedAccountsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.unlinkAccount.mockResolvedValue({ error: null });
  });

  it("unlinks Better Auth 1.7 accounts by their local row id", async () => {
    const onDisconnect = vi.fn();
    const user = userEvent.setup();

    render(
      <ConnectedAccountsSection
        accounts={[{ providerId: "github", accountId: "account-row-id" }]}
        onDisconnect={onDisconnect}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() => {
      expect(mocks.unlinkAccount).toHaveBeenCalledExactlyOnceWith({
        accountId: "account-row-id",
      });
      expect(onDisconnect).toHaveBeenCalledExactlyOnceWith("github");
    });
  });
});
