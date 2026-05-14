"use client";

import { createContext, useContext, useEffect, useState, useMemo, useCallback, type ReactNode } from "react";
import { useLingui } from "@lingui/react/macro";
import { getCurrencyRates, type CurrencyRate } from "@/lib/actions/search";
import { formatSalary, type PeriodLabel, type SalaryPeriod } from "@/lib/salary";

interface SalaryDisplayContextValue {
  displayCurrency: string | null;
  displayPeriod: SalaryPeriod | null;
  rates: CurrencyRate[];
  format: (min: number | null, max: number | null, currency: string | null, period: string | null) => string;
  /** Update display preferences (called from settings). */
  update: (opts: { displayCurrency?: string | null; salaryPeriod?: string | null }) => void;
}

const SalaryDisplayContext = createContext<SalaryDisplayContextValue>({
  displayCurrency: null,
  displayPeriod: null,
  rates: [],
  format: (min, max, currency, period) => formatSalary(min, max, currency, period),
  update: () => {},
});

export function SalaryDisplayProvider({
  displayCurrency: initialCurrency = null,
  salaryPeriod: initialPeriod = null,
  children,
}: {
  displayCurrency?: string | null;
  salaryPeriod?: string | null;
  children: ReactNode;
}) {
  const { t } = useLingui();
  const [rates, setRates] = useState<CurrencyRate[]>([]);
  const [displayCurrency, setDisplayCurrency] = useState(initialCurrency);
  const [salaryPeriod, setSalaryPeriod] = useState(initialPeriod);

  useEffect(() => {
    getCurrencyRates().then(setRates);
  }, []);

  const displayPeriod = (salaryPeriod as SalaryPeriod | null) ?? null;

  const update = useCallback((opts: { displayCurrency?: string | null; salaryPeriod?: string | null }) => {
    if (opts.displayCurrency !== undefined) setDisplayCurrency(opts.displayCurrency);
    if (opts.salaryPeriod !== undefined) setSalaryPeriod(opts.salaryPeriod);
  }, []);

  // Locale-aware period suffix used when the salary is shown as
  // "<amount> <CCY> / <period>" on posting cards and detail pages.
  const periodLabel: PeriodLabel = useCallback((p) => {
    switch (p) {
      case "yearly":
        return t({ id: "common.salary.period.yearly", comment: "Salary period suffix shown after the amount, e.g. '50k EUR / yearly'", message: "yearly" });
      case "monthly":
        return t({ id: "common.salary.period.monthly", comment: "Salary period suffix shown after the amount, e.g. '5k EUR / monthly'", message: "monthly" });
      case "daily":
        return t({ id: "common.salary.period.daily", comment: "Salary period suffix shown after the amount, e.g. '300 EUR / daily'", message: "daily" });
      case "hourly":
        return t({ id: "common.salary.period.hourly", comment: "Salary period suffix shown after the amount, e.g. '26 USD / hourly'", message: "hourly" });
    }
  }, [t]);

  const value = useMemo<SalaryDisplayContextValue>(() => ({
    displayCurrency,
    displayPeriod,
    rates,
    format: (min, max, currency, period) =>
      formatSalary(min, max, currency, period, {
        displayCurrency,
        displayPeriod,
        rates,
        periodLabel,
      }),
    update,
  }), [displayCurrency, displayPeriod, rates, update, periodLabel]);

  return (
    <SalaryDisplayContext.Provider value={value}>
      {children}
    </SalaryDisplayContext.Provider>
  );
}

export function useSalaryDisplay() {
  return useContext(SalaryDisplayContext);
}

/**
 * Returns the cached currency-rate table fetched once by
 * `SalaryDisplayProvider` on mount.
 *
 * Consumers (search page, company page, salary modal) historically each
 * fired their own `getCurrencyRates()` server action on mount, producing
 * three identical round-trips per `/explore` or `/company/<slug>` view
 * (~90–240ms of serial latency before salary filters were interactive —
 * see #3181). Reading the rates from context collapses that to a single
 * fetch.
 *
 * Returns `[]` when no provider is in scope (the context default), so
 * callers can treat the result as a graceful empty list. The salary
 * conversion helpers (`toEur`, `fromEur`) already fall back to an
 * identity transform on an empty rate table, so an unmounted provider
 * is non-fatal.
 */
export function useSalaryRates(): CurrencyRate[] {
  return useContext(SalaryDisplayContext).rates;
}
