"use client";

import { useState, useRef } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react";
import { t } from "@lingui/core/macro";

interface SalaryOverrideProps {
  crawlerSalary: {
    min: number | null;
    max: number | null;
    currency: string | null;
    period: string | null;
  };
  override: {
    min: number | null;
    max: number | null;
    currency: string | null;
    period: string | null;
  };
  onSave: (data: {
    salaryMin: number | null;
    salaryMax: number | null;
    currency: string | null;
    period: string | null;
  }) => void;
}

export function SalaryOverride({
  crawlerSalary,
  override,
  onSave,
}: SalaryOverrideProps) {
  const [min, setMin] = useState(override.min?.toString() ?? "");
  const [max, setMax] = useState(override.max?.toString() ?? "");
  const [currency, setCurrency] = useState(
    override.currency ?? crawlerSalary.currency ?? "EUR",
  );
  const [period, setPeriod] = useState(
    override.period ?? crawlerSalary.period ?? "yearly",
  );
  const saveTimeout = useRef<ReturnType<typeof setTimeout>>(undefined);
  useLingui();
  const minLabel = t({ id: "myJobs.salary.min", comment: "Minimum salary label", message: "Min" });
  const maxLabel = t({ id: "myJobs.salary.max", comment: "Maximum salary label", message: "Max" });
  const yearlyLabel = t({ id: "myJobs.salary.yearly", comment: "Yearly salary period option", message: "Yearly" });
  const monthlyLabel = t({ id: "myJobs.salary.monthly", comment: "Monthly salary period option", message: "Monthly" });
  const hourlyLabel = t({ id: "myJobs.salary.hourly", comment: "Hourly salary period option", message: "Hourly" });

  function handleChange(
    field: "min" | "max" | "currency" | "period",
    value: string,
  ) {
    if (field === "min") setMin(value);
    if (field === "max") setMax(value);
    if (field === "currency") setCurrency(value);
    if (field === "period") setPeriod(value);

    // Debounced auto-save
    clearTimeout(saveTimeout.current);
    saveTimeout.current = setTimeout(() => {
      const newMin = field === "min" ? value : min;
      const newMax = field === "max" ? value : max;
      const newCurrency = field === "currency" ? value : currency;
      const newPeriod = field === "period" ? value : period;
      onSave({
        salaryMin: newMin ? parseInt(newMin, 10) || null : null,
        salaryMax: newMax ? parseInt(newMax, 10) || null : null,
        currency: newCurrency || null,
        period: newPeriod || null,
      });
    }, 800);
  }

  return (
    <div className="space-y-2">
      <h3 className="text-[10px] font-medium uppercase tracking-wider text-muted">
        <Trans
          id="myJobs.detail.salary"
          comment="Salary override section heading"
        >
          Salary
        </Trans>
      </h3>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="mb-0.5 block text-[10px] text-muted">{minLabel}</label>
          <input
            type="number"
            value={min}
            onChange={(e) => handleChange("min", e.target.value)}
            placeholder={crawlerSalary.min?.toLocaleString() ?? "—"}
            className="w-full rounded border border-border-soft bg-surface px-2 py-1 text-xs placeholder:text-muted/50"
          />
        </div>
        <div>
          <label className="mb-0.5 block text-[10px] text-muted">{maxLabel}</label>
          <input
            type="number"
            value={max}
            onChange={(e) => handleChange("max", e.target.value)}
            placeholder={crawlerSalary.max?.toLocaleString() ?? "—"}
            className="w-full rounded border border-border-soft bg-surface px-2 py-1 text-xs placeholder:text-muted/50"
          />
        </div>
        <div>
          <label className="mb-0.5 block text-[10px] text-muted">
            <Trans
              id="myJobs.detail.salary.currency"
              comment="Currency label in salary override"
            >
              Currency
            </Trans>
          </label>
          <input
            type="text"
            value={currency}
            onChange={(e) =>
              handleChange("currency", e.target.value.toUpperCase())
            }
            placeholder={crawlerSalary.currency ?? "EUR"}
            maxLength={3}
            className="w-full rounded border border-border-soft bg-surface px-2 py-1 text-xs uppercase placeholder:text-muted/50"
          />
        </div>
        <div>
          <label className="mb-0.5 block text-[10px] text-muted">
            <Trans
              id="myJobs.detail.salary.period"
              comment="Period label in salary override"
            >
              Period
            </Trans>
          </label>
          <select
            value={period}
            onChange={(e) => handleChange("period", e.target.value)}
            className="w-full rounded border border-border-soft bg-surface px-2 py-1 text-xs"
          >
            <option value="yearly">{yearlyLabel}</option>
            <option value="monthly">{monthlyLabel}</option>
            <option value="hourly">{hourlyLabel}</option>
          </select>
        </div>
      </div>
    </div>
  );
}
