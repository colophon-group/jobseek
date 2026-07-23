/**
 * Tests for SalaryDisplayProvider — issue #3181.
 *
 * The `/explore` and `/company/<slug>` pages historically fired
 * `getCurrencyRates()` three times per view (SalaryDisplayProvider +
 * SearchPage/CompanyPage + SalaryModal). The fix hoists the fetch into
 * the provider and exposes the table via a new `useSalaryRates()` hook.
 * These tests pin that contract:
 *
 *   1. mounting the provider triggers exactly one fetch
 *   2. multiple consumers reading via the hook share the same fetch
 *   3. consumers without a provider in scope still mount and receive
 *      a graceful empty table (no fallback fetch, no crash)
 */
import { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const getCurrencyRatesMock = vi.fn();

// The real `@/lib/actions/search` is a server action that transitively
// imports `server-only`, which throws when loaded outside a Next runtime.
vi.mock("server-only", () => ({}));
vi.mock("@/lib/actions/search", () => ({
  getCurrencyRates: (...args: unknown[]) => getCurrencyRatesMock(...args),
}));

// Import after the mock is installed so the provider closes over the
// mocked module.
import {
  SalaryDisplayProvider,
  useSalaryRates,
  useSalaryDisplay,
} from "../providers/SalaryDisplayProvider";

function RatesProbe({ testId }: { testId: string }) {
  const rates = useSalaryRates();
  return (
    <span data-testid={testId}>
      {rates.map((r) => `${r.currency}:${r.toEur}`).join(",")}
    </span>
  );
}

function FormatterProbe() {
  const { rates } = useSalaryDisplay();
  // Consume rates through the legacy `useSalaryDisplay()` API as well —
  // it must read from the same context value, not double-fetch.
  return (
    <span data-testid="formatter-rates">{rates.length}</span>
  );
}

function PreferenceProbe() {
  const salary = useSalaryDisplay();
  return (
    <>
      <span data-testid="currency">{salary.displayCurrency ?? "none"}</span>
      <span data-testid="period">{salary.displayPeriod ?? "original"}</span>
      <button
        type="button"
        onClick={() =>
          salary.update({ displayCurrency: "CHF", salaryPeriod: "hourly" })
        }
      >
        Update salary display
      </button>
    </>
  );
}

function BootstrapPreferenceHarness() {
  const [preferences, setPreferences] = useState<{
    currency: string | null;
    period: string | null;
  }>({ currency: null, period: null });

  return (
    <>
      <button
        type="button"
        onClick={() => setPreferences({ currency: "GBP", period: "yearly" })}
      >
        Load account preferences
      </button>
      <SalaryDisplayProvider
        displayCurrency={preferences.currency}
        salaryPeriod={preferences.period}
      >
        <PreferenceProbe />
      </SalaryDisplayProvider>
    </>
  );
}

beforeEach(() => {
  getCurrencyRatesMock.mockReset();
  const memory = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return memory.size;
    },
    clear: () => memory.clear(),
    getItem: (key: string) => memory.get(key) ?? null,
    key: (index: number) => Array.from(memory.keys())[index] ?? null,
    removeItem: (key: string) => {
      memory.delete(key);
    },
    setItem: (key: string, value: string) => {
      memory.set(key, value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: stub,
  });
});

describe("SalaryDisplayProvider rate sharing (issue #3181)", () => {
  it("fetches currency rates exactly once when multiple consumers mount", async () => {
    getCurrencyRatesMock.mockResolvedValue([
      { currency: "EUR", toEur: 1 },
      { currency: "USD", toEur: 0.92 },
    ]);

    render(
      <SalaryDisplayProvider>
        <RatesProbe testId="probe-a" />
        <RatesProbe testId="probe-b" />
        <RatesProbe testId="probe-c" />
        <FormatterProbe />
      </SalaryDisplayProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("probe-a").textContent).toContain("EUR:1");
    });

    // All consumers see the same payload — and we only paid for one
    // round-trip to do it.
    expect(getCurrencyRatesMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("probe-b").textContent).toBe("EUR:1,USD:0.92");
    expect(screen.getByTestId("probe-c").textContent).toBe("EUR:1,USD:0.92");
    expect(screen.getByTestId("formatter-rates").textContent).toBe("2");
  });

  it("returns an empty rate list when no provider is in scope (no fallback fetch, no crash)", () => {
    // No mock setup: if the hook fell through to its own fetch, the
    // assertion below would still pass (mock returns undefined), but
    // `getCurrencyRatesMock.toHaveBeenCalledTimes(0)` would catch it.
    render(<RatesProbe testId="orphan" />);

    expect(screen.getByTestId("orphan").textContent).toBe("");
    expect(getCurrencyRatesMock).toHaveBeenCalledTimes(0);
  });
});

describe("SalaryDisplayProvider preference persistence (#6035)", () => {
  it("rehydrates anonymous preferences and persists updates across remounts", async () => {
    getCurrencyRatesMock.mockResolvedValue([]);
    window.localStorage.setItem("pref-display-currency", "USD");
    window.localStorage.setItem("pref-salary-period", "monthly");

    const first = render(
      <SalaryDisplayProvider>
        <PreferenceProbe />
      </SalaryDisplayProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("currency").textContent).toBe("USD");
      expect(screen.getByTestId("period").textContent).toBe("monthly");
    });

    fireEvent.click(screen.getByRole("button", { name: "Update salary display" }));
    expect(window.localStorage.getItem("pref-display-currency")).toBe("CHF");
    expect(window.localStorage.getItem("pref-salary-period")).toBe("hourly");
    first.unmount();

    render(
      <SalaryDisplayProvider>
        <PreferenceProbe />
      </SalaryDisplayProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("currency").textContent).toBe("CHF");
      expect(screen.getByTestId("period").textContent).toBe("hourly");
    });
  });

  it("lets asynchronously loaded account preferences override anonymous values", async () => {
    getCurrencyRatesMock.mockResolvedValue([]);
    window.localStorage.setItem("pref-display-currency", "USD");
    window.localStorage.setItem("pref-salary-period", "monthly");

    render(<BootstrapPreferenceHarness />);
    await waitFor(() => {
      expect(screen.getByTestId("currency").textContent).toBe("USD");
      expect(screen.getByTestId("period").textContent).toBe("monthly");
    });

    fireEvent.click(screen.getByRole("button", { name: "Load account preferences" }));
    await waitFor(() => {
      expect(screen.getByTestId("currency").textContent).toBe("GBP");
      expect(screen.getByTestId("period").textContent).toBe("yearly");
    });
  });
});
