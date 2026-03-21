import type { CurrencyRate } from "@/lib/actions/search";

export type SalaryPeriod = "yearly" | "monthly" | "daily" | "hourly";

/** Multiplier to convert FROM the given period TO yearly. */
const TO_YEARLY: Record<SalaryPeriod, number> = {
  yearly: 1,
  monthly: 12,
  daily: 252,   // working days
  hourly: 2016, // 252 × 8
};

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

export function formatSalary(
  min: number | null,
  max: number | null,
  currency: string | null,
  period: string | null,
  opts?: {
    displayCurrency?: string | null;
    displayPeriod?: SalaryPeriod | null;
    rates?: CurrencyRate[];
  },
): string {
  if (min == null && max == null) return "";

  const fromCur = currency ?? "EUR";
  const fromPeriod = normalizePeriod(period);
  const toCur = opts?.displayCurrency && opts.rates?.length ? opts.displayCurrency : fromCur;
  const toPeriod = opts?.displayPeriod ?? fromPeriod;
  const rates = opts?.rates ?? [];

  const conv = (n: number) => convertAmount(n, fromCur, toCur, fromPeriod, toPeriod, rates);
  const fmt = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

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
    parts.push(`/ ${toPeriod}`);
  }

  return parts.join(" ");
}

function normalizePeriod(period: string | null): SalaryPeriod {
  if (period === "monthly" || period === "daily" || period === "hourly") return period;
  return "yearly";
}
