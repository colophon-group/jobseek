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

/**
 * Result of a salary conversion. The `currency` field signals whether the
 * conversion actually happened in the requested target currency, or whether
 * we bailed out and kept the source currency (issue #3184).
 *
 * A bailed-out result occurs when either `fromCurrency` or `toCurrency` is
 * missing from `rates` — the old behavior silently substituted a 1:1 rate,
 * which made e.g. ¥5,000,000 render as "5,000k EUR". Callers MUST check the
 * returned `currency` and render accordingly instead of assuming the original
 * target was used.
 */
export interface ConvertedAmount {
  amount: number;
  currency: string;
}

export function convertAmount(
  amount: number,
  fromCurrency: string,
  toCurrency: string,
  fromPeriod: SalaryPeriod,
  toPeriod: SalaryPeriod,
  rates: CurrencyRate[],
): ConvertedAmount {
  let val = amount;
  let currency = toCurrency;

  // Currency conversion: source → EUR → target.
  // If either the source or target rate is missing, bail out and keep the
  // amount in its source currency. The pre-fix `?? 1` fallback silently
  // produced a 1:1 conversion that mislabeled the result (issue #3184).
  if (fromCurrency !== toCurrency) {
    const fromRate = rates.find((r) => r.currency === fromCurrency)?.toEur;
    const toRate = rates.find((r) => r.currency === toCurrency)?.toEur;
    if (fromRate == null || toRate == null) {
      currency = fromCurrency;
    } else {
      val = val * fromRate / toRate;
    }
  }

  // Period conversion: source → yearly → target.
  // Period conversion is currency-independent and always safe to apply.
  if (fromPeriod !== toPeriod) {
    val = val * TO_YEARLY[fromPeriod] / TO_YEARLY[toPeriod];
  }

  return { amount: Math.round(val), currency };
}

/**
 * Convert a salary-filter amount (in the user's display currency) to EUR so it
 * can be compared against the EUR-indexed `salary_eur` field on every
 * `job_posting` Typesense document (issue #3178).
 *
 * The crawler computes `salary_eur = annual_min * to_eur` in
 * `apps/crawler/src/processing/cpu.py::_extract_salary_fields`, so the filter
 * threshold MUST be in the same EUR-equivalent units. Before this helper,
 * `salaryMinEur` was assigned directly from the user-currency amount, which
 * silently excluded postings whose source currency was weaker than EUR
 * (e.g. "$100K USD" filtered out US roles paying $100K because their
 * `salary_eur` ≈ 92,000 < 100,000).
 *
 * Behavior:
 * - `amount` null/undefined → returned unchanged (no filter to apply).
 * - `fromCurrency === "EUR"` → identity (already in EUR).
 * - Known rate → `amount * rates[fromCurrency].toEur`.
 * - Missing rate (unsupported currency) → log warning, return `amount`
 *   unchanged. This preserves the pre-#3178 behavior for unknown currencies
 *   rather than silently filtering everything to zero.
 */
export function convertToEur(
  amount: number | undefined,
  fromCurrency: string,
  rates: CurrencyRate[],
): number | undefined {
  if (amount == null) return amount;
  if (fromCurrency === "EUR") return amount;
  const rate = rates.find((r) => r.currency === fromCurrency)?.toEur;
  if (rate == null) {
    console.warn(
      `[convertToEur] missing currency_rate for ${fromCurrency}; passing amount through unchanged`,
    );
    return amount;
  }
  return Math.round(amount * rate);
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

  // The currency label rendered after the amount. If convertAmount bailed
  // (missing FX rate), both bounds will agree on the source currency since
  // they share the same fromCur/toCur. Prefer cMin's resolved currency,
  // falling back to cMax's. This guards against the #3184 "5M JPY → 5,000k
  // EUR" bug: when rates are missing, we render the source currency, not
  // the requested target.
  const resolvedCurrency = cMin?.currency ?? cMax?.currency ?? toCur;

  const parts: string[] = [];
  if (cMin != null && cMax != null) {
    parts.push(`${fmt(cMin.amount)}–${fmt(cMax.amount)} ${resolvedCurrency}`);
  } else if (cMin != null) {
    parts.push(`${fmt(cMin.amount)}+ ${resolvedCurrency}`);
  } else if (cMax != null) {
    parts.push(`≤${fmt(cMax.amount)} ${resolvedCurrency}`);
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
