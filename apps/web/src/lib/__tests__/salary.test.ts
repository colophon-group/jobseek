import { describe, it, expect } from "vitest";
import { formatSalary, decodeStoredAmount, type PeriodLabel } from "../salary";

describe("decodeStoredAmount", () => {
  it("divides hourly amounts by 100 (cents → whole units)", () => {
    expect(decodeStoredAmount(2550, "hourly")).toBe(25.5);
    expect(decodeStoredAmount(1425, "hourly")).toBe(14.25);
  });

  it("leaves non-hourly amounts untouched", () => {
    expect(decodeStoredAmount(120000, "yearly")).toBe(120000);
    expect(decodeStoredAmount(5000, "monthly")).toBe(5000);
    expect(decodeStoredAmount(300, "daily")).toBe(300);
  });
});

describe("formatSalary — #3174 hourly cents bug", () => {
  // Crawler stores hourly salaries in cents: $25.50/hr → 2550, $14.25/hr → 1425.
  // Before the fix, formatSalary treated these as whole-unit amounts and
  // produced "3k USD / hourly" instead of "26 USD / hourly".

  it("formats $25.50/hr stored as 2550 cents as 26 USD / hourly (not 3k)", () => {
    const out = formatSalary(2550, null, "USD", "hourly");
    expect(out).toBe("26+ USD / hourly");
    expect(out).not.toContain("k");
  });

  it("formats $14.25/hr stored as 1425 cents as 14 USD / hourly (not 1k or 1425)", () => {
    const out = formatSalary(1425, null, "USD", "hourly");
    expect(out).toBe("14+ USD / hourly");
    expect(out).not.toContain("k");
    expect(out).not.toContain("1425");
  });

  it("formats hourly range $37-$65/hr (3700-6500 in cents) as 37–65 USD / hourly", () => {
    const out = formatSalary(3700, 6500, "USD", "hourly");
    expect(out).toBe("37–65 USD / hourly");
  });

  it("does not divide yearly amounts (regression check)", () => {
    const out = formatSalary(120000, 150000, "USD", "yearly");
    expect(out).toBe("120k–150k USD");
  });

  it("does not divide monthly amounts (regression check)", () => {
    const out = formatSalary(5000, null, "EUR", "monthly");
    expect(out).toBe("5k+ EUR / monthly");
  });
});

describe("formatSalary — #3144 period suffix i18n", () => {
  // The default period label is English. When the SalaryDisplayProvider
  // injects a `periodLabel` (via useLingui), the output uses translated terms.

  it("uses English period suffix by default", () => {
    expect(formatSalary(120000, 150000, "USD", "yearly", { displayPeriod: "yearly" })).toBe("120k–150k USD");
    expect(formatSalary(5000, null, "EUR", "monthly", { displayPeriod: "monthly" })).toBe("5k+ EUR / monthly");
    expect(formatSalary(2550, null, "USD", "hourly", { displayPeriod: "hourly" })).toBe("26+ USD / hourly");
  });

  it("uses injected periodLabel for the suffix (German)", () => {
    const deLabel: PeriodLabel = (p) =>
      ({ yearly: "jährlich", monthly: "monatlich", daily: "täglich", hourly: "stündlich" })[p];
    expect(formatSalary(5000, null, "EUR", "monthly", { periodLabel: deLabel })).toBe("5k+ EUR / monatlich");
    expect(formatSalary(2550, null, "USD", "hourly", { periodLabel: deLabel })).toBe("26+ USD / stündlich");
  });

  it("uses injected periodLabel for the suffix (French)", () => {
    const frLabel: PeriodLabel = (p) =>
      ({ yearly: "par an", monthly: "par mois", daily: "par jour", hourly: "par heure" })[p];
    expect(formatSalary(2550, null, "USD", "hourly", { periodLabel: frLabel })).toBe("26+ USD / par heure");
  });

  it("uses injected periodLabel for the suffix (Italian)", () => {
    const itLabel: PeriodLabel = (p) =>
      ({ yearly: "annuale", monthly: "mensile", daily: "giornaliero", hourly: "orario" })[p];
    expect(formatSalary(2550, null, "USD", "hourly", { periodLabel: itLabel })).toBe("26+ USD / orario");
  });

  it("omits the suffix when the period is yearly (no '/ yearly' clutter)", () => {
    const deLabel: PeriodLabel = (p) =>
      ({ yearly: "jährlich", monthly: "monatlich", daily: "täglich", hourly: "stündlich" })[p];
    const out = formatSalary(120000, 150000, "USD", "yearly", { periodLabel: deLabel });
    expect(out).toBe("120k–150k USD");
    expect(out).not.toContain("jährlich");
  });
});

describe("formatSalary — null / empty handling (regression)", () => {
  it("returns empty string when min and max are both null", () => {
    expect(formatSalary(null, null, "USD", "yearly")).toBe("");
  });

  it("handles max-only", () => {
    expect(formatSalary(null, 150000, "USD", "yearly")).toBe("≤150k USD");
  });
});
