import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  createPortalSession: vi.fn(),
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({
    isLoggedIn: true,
  }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/actions/billing", () => ({
  createPortalSession: mocks.createPortalSession,
}));

import { BillingSettings } from "../BillingSettings";

describe("BillingSettings action errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createPortalSession.mockResolvedValue({ url: null });
  });

  it("offers no purchase action while Pro billing is unavailable", () => {
    render(
      <BillingSettings
        planInfo={{ plan: "free", canReceiveAlerts: false }}
      />,
    );

    expect(screen.getByText("Plan details coming soon")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upgrade to Pro" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Upgrade to Pro" })).toBeNull();
  });

  it("translates portal error codes before rendering them", async () => {
    mocks.createPortalSession.mockResolvedValueOnce({
      url: null,
      error: "billing_account_not_found",
    });

    render(
      <BillingSettings
        planInfo={{ plan: "unlimited", canReceiveAlerts: true }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Manage subscription" }));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toBe(
        "No billing account found.",
      );
    });
  });
});
