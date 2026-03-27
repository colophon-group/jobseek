"use client";

import { createContext, useContext, useEffect, useState, useMemo, useCallback, type ReactNode } from "react";
import { getCurrencyRates, type CurrencyRate } from "@/lib/actions/search";
import { formatSalary, type SalaryPeriod } from "@/lib/salary";

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

  const value = useMemo<SalaryDisplayContextValue>(() => ({
    displayCurrency,
    displayPeriod,
    rates,
    format: (min, max, currency, period) =>
      formatSalary(min, max, currency, period, {
        displayCurrency,
        displayPeriod,
        rates,
      }),
    update,
  }), [displayCurrency, displayPeriod, rates, update]);

  return (
    <SalaryDisplayContext.Provider value={value}>
      {children}
    </SalaryDisplayContext.Provider>
  );
}

export function useSalaryDisplay() {
  return useContext(SalaryDisplayContext);
}
