import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
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
    isLoggedIn: true,
  }),
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => `/en${path}`,
}));

vi.mock("@/lib/actions/billing", () => ({
  createPortalSession: mocks.createPortalSession,
  createCheckoutSession: mocks.createCheckoutSession,
}));

vi.mock("@/lib/paddle/browser", () => ({ loadPaddle: async () => ({ Checkout: { open: mocks.open } }) }));

import { BillingSettings } from "../BillingSettings";

describe("BillingSettings action errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.searchParams = new URLSearchParams();
    mocks.createPortalSession.mockResolvedValue({ url: null });
  });

  it("returns a newly subscribed viewer to the preserved focused search", async () => {
    mocks.searchParams = new URLSearchParams({
      next: "/en/explore?q=engineer&loc=switzerland&narrow=1",
    });

    render(
      <BillingSettings
        planInfo={{ plan: "unlimited" }}
      />,
    );

    await waitFor(() => {
      expect(mocks.replace).toHaveBeenCalledWith(
        "/en/explore?q=engineer&loc=switzerland&narrow=1",
      );
    });
  });

  it("opens the server transaction and preserves the return destination", async () => {
    mocks.searchParams = new URLSearchParams({ next: "/en/explore?q=engineer" });
    mocks.createCheckoutSession.mockResolvedValue({ transactionId: "txn_verified", email: "test@example.com" });
    render(<BillingSettings planInfo={{ plan: "free", checkoutEnabled: true }} />);
    await userEvent.click(screen.getByRole("button", { name: "Start 7-day free trial" }));
    await waitFor(() => expect(mocks.open).toHaveBeenCalledWith(expect.objectContaining({ transactionId: "txn_verified" })));
    const url = new URL(mocks.open.mock.calls[0][0].settings.successUrl);
    expect(url.searchParams.get("next")).toBe("/en/explore?q=engineer");
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

    expect(screen.getByText("AI filtering for your watchlists")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upgrade to Pro" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Upgrade to Pro" })).toBeNull();
  });

  it.each([
    ["de", "Bis zu 10 Watchlists", "Unternehmen favorisieren", "Alles aus dem kostenlosen Tarif"],
    ["fr", "Jusqu’à 10 watchlists", "Ajouter des entreprises aux favoris", "Tout ce qui est inclus dans l’offre gratuite"],
    ["it", "Fino a 10 watchlist", "Aggiungi aziende ai preferiti", "Tutto ciò che è incluso nel piano Free"],
  ])("keeps the %s billing catalog free of retired restrictions", (locale, limit, star, included) => {
    const catalog = readFileSync(join(process.cwd(), "locales", `${locale}.po`), "utf8");
    expect(catalog).toContain(`msgid "settings.billing.free.f0"\nmsgstr "${limit}"`);
    expect(catalog).toContain(`msgid "settings.billing.free.f1"\nmsgstr "${star}"`);
    expect(catalog).toContain(`msgid "settings.billing.pro.f2"\nmsgstr "${included}"`);
  });

  it("translates portal error codes before rendering them", async () => {
    mocks.createPortalSession.mockResolvedValueOnce({
      url: null,
      error: "billing_account_not_found",
    });

    render(
      <BillingSettings
        planInfo={{ plan: "unlimited" }}
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
