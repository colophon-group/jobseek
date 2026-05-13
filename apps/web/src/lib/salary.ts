import type { CurrencyRate } from "@/lib/actions/search";

export type SalaryPeriod = "yearly" | "monthly" | "daily" | "hourly";

/**
 * Multiplier to convert FROM the given period TO yearly.
 *
 * Source of truth for hourly annualization: the crawler computes the
 * `salary_eur` filter column with **2080 hours/year** (52 × 40) in
 * `apps/crawler/src/processing/cpu.py::_extract_salary_fields`. The web
 * MUST use the same constant so the displayed yearly-equivalent of an
 * hourly posting matches the cutoff the salary slider is filtering on.
 * See issue #3194.
 */
const TO_YEARLY: Record<SalaryPeriod, number> = {
  yearly: 1,
  monthly: 12,
  daily: 252,   // working days (web-only; crawler does not emit "daily")
  hourly: 2080, // 52 × 40 — must match crawler cpu.py
};

/**
 * Crawler-side encoding: hourly salaries are stored in **cents** (the smallest
 * monetary unit of the source currency) so they can be exact integers
 * (e.g. $25.50/hr → 2550). All other periods are stored as whole units.
 * See `apps/crawler/src/processing/cpu.py::_extract_salary_fields` and the
 * `SalaryRange` dataclass in `apps/crawler/src/core/salary_extract.py`.
 *
 * The DB stores raw values; consumers must scale back when the period is
 * `hourly`. Returns the value in whole units (e.g. 25.50 for "$25.50/hr").
 */
export function decodeStoredAmount(amount: number, period: SalaryPeriod): number {
  return period === "hourly" ? amount / 100 : amount;
}

export function convertAmount(
  amount: number,
  fromCurrency: string,
  toCurrency: string,
  fromPeriod: SalaryPeriod,
  toPeriod: SalaryPeriod,
  rates: CurrencyRate[],
): number {
  let val = amount;

  // Currency conversion: source → EUR → target
  if (fromCurrency !== toCurrency) {
    const fromRate = rates.find((r) => r.currency === fromCurrency)?.toEur ?? 1;
    const toRate = rates.find((r) => r.currency === toCurrency)?.toEur ?? 1;
    val = val * fromRate / toRate;
  }

  // Period conversion: source → yearly → target
  if (fromPeriod !== toPeriod) {
    val = val * TO_YEARLY[fromPeriod] / TO_YEARLY[toPeriod];
  }

  return Math.round(val);
}

/**
 * Period-suffix label provider. Callers in client components inject a
 * Lingui-translated label per period; default falls back to English so
 * pure-function consumers (tests, server scripts) still work.
 */
export type PeriodLabel = (period: SalaryPeriod) => string;

const DEFAULT_PERIOD_LABEL: PeriodLabel = (p) => p;

export function formatSalary(
  min: number | null,
  max: number | null,
  currency: string | null,
  period: string | null,
  opts?: {
    displayCurrency?: string | null;
    displayPeriod?: SalaryPeriod | null;
    rates?: CurrencyRate[];
    periodLabel?: PeriodLabel;
  },
): string {
  if (min == null && max == null) return "";

  const fromCur = currency ?? "EUR";
  const fromPeriod = normalizePeriod(period);
  const toCur = opts?.displayCurrency && opts.rates?.length ? opts.displayCurrency : fromCur;
  const toPeriod = opts?.displayPeriod ?? fromPeriod;
  const rates = opts?.rates ?? [];
  const periodLabel = opts?.periodLabel ?? DEFAULT_PERIOD_LABEL;

  // Decode the storage convention: hourly values come in cents.
  // Do this BEFORE currency/period conversion so downstream math is in whole units.
  const decode = (n: number) => decodeStoredAmount(n, fromPeriod);
  const conv = (n: number) => convertAmount(decode(n), fromCur, toCur, fromPeriod, toPeriod, rates);
  const fmt = (n: number) => {
    if (toPeriod === "hourly") {
      // For hourly rates, the natural unit is a small whole number — round to integer.
      return String(Math.round(n));
    }
    return n >= 1000 ? `${Math.round(n / 1000)}k` : String(Math.round(n));
  };

  const cMin = min != null ? conv(min) : null;
  const cMax = max != null ? conv(max) : null;

  const parts: string[] = [];
  if (cMin != null && cMax != null) {
    parts.push(`${fmt(cMin)}–${fmt(cMax)} ${toCur}`);
  } else if (cMin != null) {
    parts.push(`${fmt(cMin)}+ ${toCur}`);
  } else if (cMax != null) {
    parts.push(`≤${fmt(cMax)} ${toCur}`);
  }

  if (toPeriod !== "yearly") {
    parts.push(`/ ${periodLabel(toPeriod)}`);
  }

  return parts.join(" ");
}

function normalizePeriod(period: string | null): SalaryPeriod {
  if (period === "monthly" || period === "daily" || period === "hourly") return period;
  return "yearly";
}
