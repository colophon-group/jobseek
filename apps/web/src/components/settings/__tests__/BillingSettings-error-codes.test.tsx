import { readFileSync } from "node:fs";
import { join } from "node:path";
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

  it.each([
    ["de", "Bis zu 10 Watchlists", "Unternehmen favorisieren", "Tarifdetails folgen", "Alles aus dem kostenlosen Tarif"],
    ["fr", "Jusqu’à 10 watchlists", "Ajouter des entreprises aux favoris", "Détails du forfait à venir", "Tout ce qui est inclus dans l’offre gratuite"],
    ["it", "Fino a 10 watchlist", "Aggiungi aziende ai preferiti", "Dettagli del piano in arrivo", "Tutto ciò che è incluso nel piano Free"],
  ])("keeps the %s billing catalog free of retired restrictions", (locale, limit, star, details, included) => {
    const catalog = readFileSync(join(process.cwd(), "locales", `${locale}.po`), "utf8");
    expect(catalog).toContain(`msgid "settings.billing.free.f0"\nmsgstr "${limit}"`);
    expect(catalog).toContain(`msgid "settings.billing.free.f1"\nmsgstr "${star}"`);
    expect(catalog).toContain(`msgid "settings.billing.pro.f1"\nmsgstr "${details}"`);
    expect(catalog).toContain(`msgid "settings.billing.pro.f2"\nmsgstr "${included}"`);
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
