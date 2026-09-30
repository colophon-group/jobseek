import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  isLoggedIn: true,
  createPortalSession: vi.fn(),
  createCheckoutSession: vi.fn(),
  open: vi.fn(),
  replace: vi.fn(),
  searchParams: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => mocks.searchParams,
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({
    isLoggedIn: mocks.isLoggedIn,
  }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/actions/billing", () => ({
  createPortalSession: mocks.createPortalSession,
  createCheckoutSession: mocks.createCheckoutSession,
}));

vi.mock("@/lib/actions/pro-waitlist", () => ({ joinProWaitlist: vi.fn() }));

vi.mock("@/lib/actions/ai-filter", () => ({
  configureAiFilter: vi.fn(),
  createAiFilteredWatchlist: vi.fn(),
  disableAiFilter: vi.fn(),
}));


import { BillingSettings } from "../BillingSettings";

describe("BillingSettings action errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isLoggedIn = true;
    mocks.searchParams = new URLSearchParams();
    mocks.createPortalSession.mockResolvedValue({ url: null });
  });

  it("returns a newly subscribed viewer to the preserved focused search", async () => {
    mocks.searchParams = new URLSearchParams({
      next: "/en/explore?q=engineer&loc=switzerland&narrow=1",
    });

    render(
      <BillingSettings
        planInfo={{ plan: "unlimited", hasBillingAccount: true }}
      />,
    );

    await waitFor(() => {
      expect(mocks.replace).toHaveBeenCalledWith(
        "/en/explore?q=engineer&loc=switzerland&narrow=1",
      );
    });
  });

  it("redirects to the server Checkout URL and sends the return destination", async () => {
    mocks.searchParams = new URLSearchParams({ next: "/en/explore?q=engineer" });
    mocks.createCheckoutSession.mockResolvedValue({ url: "https://checkout.stripe.com/c/pay/cs_test_verified" });
    render(<BillingSettings planInfo={{ plan: "free", checkoutEnabled: true }} />);
    await userEvent.click(screen.getByRole("button", { name: "Start 7-day free trial" }));
    await waitFor(() => expect(mocks.createCheckoutSession).toHaveBeenCalledWith("en", "/en/explore?q=engineer"));
    expect(window.location.href).toBe("https://checkout.stripe.com/c/pay/cs_test_verified");
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("retains the portal for an expired subscriber and offers no repeat trial", () => {
    render(<BillingSettings planInfo={{ plan: "free", checkoutEnabled: true, hasBillingAccount: true, trialEligible: false, status: "canceled" }} />);
    expect(screen.getByRole("button", { name: "Manage subscription" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Subscribe to Pro" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start 7-day free trial" })).toBeNull();
  });

  it("offers no purchase action while Pro billing is unavailable", () => {
    render(
      <BillingSettings
        planInfo={{ plan: "free" }}
      />,
    );

    expect(screen.getByRole("heading", { name: /Your criteria.*A shorter list/ })).toBeTruthy();
    expect(screen.getByText("Trial signup isn’t open yet.")).toBeTruthy();
    expect(screen.getByRole("form", { name: "Pro waiting list" })).toBeTruthy();
    expect(screen.queryByText(/Payment method required/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Upgrade to Pro" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Upgrade to Pro" })).toBeNull();
  });

  it("explains Narrowed before sign-in and preserves the checkout and search return paths", () => {
    mocks.isLoggedIn = false;
    mocks.searchParams = new URLSearchParams({ next: "/en/explore?q=python&narrow=1" });
    render(<BillingSettings planInfo={{ plan: "free", checkoutEnabled: true }} />);
    expect(screen.getByRole("heading", { name: /Your criteria.*A shorter list/ })).toBeTruthy();
    const link = screen.getByRole("link", { name: "Start 7-day free trial" });
    expect(screen.queryByRole("form", { name: "Pro waiting list" })).toBeNull();
    const login = new URL(link.getAttribute("href")!, "https://jseek.co");
    expect(login.pathname).toBe("/en/sign-in");
    const billing = new URL(login.searchParams.get("next")!, "https://jseek.co");
    expect(billing.pathname).toBe("/en/settings/billing");
    expect(billing.searchParams.get("next")).toBe("/en/explore?q=python&narrow=1");
  });

  it("gives subscribers a usable next step and an unambiguous end date instead of another pitch", () => {
    render(<BillingSettings planInfo={{ plan: "unlimited", status: "trialing", periodEnd: "2026-10-04T12:00:00Z", hasBillingAccount: true }} />);
    expect(screen.getByText("Free trial")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Pro waiting list" })).toBeNull();
    expect(screen.getByText("October 4, 2026")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to your watchlists" }).getAttribute("href")).toBe("/en/watchlists");
    expect(screen.queryByText("An example")).toBeNull();
    expect(screen.queryByRole("button", { name: "Start 7-day free trial" })).toBeNull();
  });

  it("directs a past-due subscriber to billing rather than another purchase", () => {
    render(<BillingSettings planInfo={{ plan: "free", status: "past_due", hasBillingAccount: true, checkoutEnabled: true }} />);
    expect(screen.getByText("Payment needs attention")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Manage subscription" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start 7-day free trial" })).toBeNull();
  });

  it("translates portal error codes before rendering them", async () => {
    mocks.createPortalSession.mockResolvedValueOnce({
      url: null,
      error: "billing_account_not_found",
    });

    render(
      <BillingSettings
        planInfo={{ plan: "unlimited", hasBillingAccount: true }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Manage subscription" }));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toBe(
        "No billing account found.",
      );
    });
  });

  it("retains billing management when an active provider period has expired locally", () => {
    render(<BillingSettings planInfo={{ plan: "free", status: "active", hasBillingAccount: true, checkoutEnabled: true, trialEligible: false }} />);
    expect(screen.getByRole("button", { name: "Manage subscription" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Subscribe to Pro" })).toBeNull();
  });

  it("gives manual grants Pro access without an unusable provider portal button", () => {
    render(<BillingSettings planInfo={{ plan: "unlimited", hasBillingAccount: false }} />);
    expect(screen.getByRole("link", { name: "Go to your watchlists" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Manage subscription" })).toBeNull();
  });
});
