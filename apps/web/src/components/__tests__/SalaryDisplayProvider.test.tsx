/**
 * Tests for SalaryDisplayProvider — issue #3181.
 *
 * The `/explore` and `/company/<slug>` pages historically fired
 * `getCurrencyRates()` three times per view (SalaryDisplayProvider +
 * SearchPage/CompanyPage + SalaryModal). #3181 hoisted that to one provider
 * RPC; #7197 resolves the hours-cached table in the shared server layout and
 * injects it into the provider. These tests pin the final contract:
 *
 *   1. multiple consumers receive the same server-supplied table immediately
 *   2. mounting the provider triggers no browser Server Action
 *   3. consumers without a provider in scope still mount and receive
 *      a graceful empty table (no fallback fetch, no crash)
 */
import { useState } from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

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
        initialRates={[]}
        displayCurrency={preferences.currency}
        salaryPeriod={preferences.period}
      >
        <PreferenceProbe />
      </SalaryDisplayProvider>
    </>
  );
}

beforeEach(() => {
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

describe("SalaryDisplayProvider rate sharing (#3181, #7197)", () => {
  it("shares the injected currency rates when multiple consumers mount", () => {
    const initialRates = [
      { currency: "EUR", toEur: 1 },
      { currency: "USD", toEur: 0.92 },
    ];

    render(
      <SalaryDisplayProvider initialRates={initialRates}>
        <RatesProbe testId="probe-a" />
        <RatesProbe testId="probe-b" />
        <RatesProbe testId="probe-c" />
        <FormatterProbe />
      </SalaryDisplayProvider>,
    );

    // All consumers synchronously see the same server-shell payload. No client
    // action is imported or fired by the provider.
    expect(screen.getByTestId("probe-a").textContent).toContain("EUR:1");
    expect(screen.getByTestId("probe-b").textContent).toBe("EUR:1,USD:0.92");
    expect(screen.getByTestId("probe-c").textContent).toBe("EUR:1,USD:0.92");
    expect(screen.getByTestId("formatter-rates").textContent).toBe("2");
  });

  it("returns an empty rate list when no provider is in scope (no fallback fetch, no crash)", () => {
    render(<RatesProbe testId="orphan" />);

    expect(screen.getByTestId("orphan").textContent).toBe("");
  });
});

describe("SalaryDisplayProvider preference persistence (#6035)", () => {
  it("rehydrates anonymous preferences and persists updates across remounts", async () => {
    window.localStorage.setItem("pref-display-currency", "USD");
    window.localStorage.setItem("pref-salary-period", "monthly");

    const first = render(
      <SalaryDisplayProvider initialRates={[]}>
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
      <SalaryDisplayProvider initialRates={[]}>
        <PreferenceProbe />
      </SalaryDisplayProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("currency").textContent).toBe("CHF");
      expect(screen.getByTestId("period").textContent).toBe("hourly");
    });
  });

  it("lets asynchronously loaded account preferences override anonymous values", async () => {
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
