"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import {
  getCurrencyRates,
  getSalaryHistogram,
  type CurrencyRate,
  type SalaryBucket,
} from "@/lib/actions/search";
import type { HistogramFilters } from "@/lib/search";
import { useSalaryDisplay } from "@/components/SalaryDisplayProvider";

// Fixed EUR buckets for the slider — 0 to 300K in 10K steps
const BUCKET_WIDTH = 10000;
const NUM_BUCKETS = 30;
const MAX_EUR = NUM_BUCKETS * BUCKET_WIDTH; // 300 000

interface SalaryModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currency: string;
  min: number | undefined;
  max: number | undefined;
  onApply: (currency: string, min: number | undefined, max: number | undefined) => void;
  histogramFilters?: HistogramFilters;
}

function formatK(v: number): string {
  if (v >= 1000) return `${Math.round(v / 1000)}K`;
  return String(v);
}

/** Convert a EUR amount to the display currency. */
function fromEur(eur: number, rate: number): number {
  return rate > 0 ? Math.round(eur / rate) : eur;
}

/** Convert a display-currency amount to EUR. */
function toEurVal(amount: number, rate: number): number {
  return rate > 0 ? Math.round(amount * rate) : amount;
}

// ── Dual-thumb range slider ────────────────────────────────────────

interface DualSliderProps {
  min: number;
  max: number;
  step: number;
  valueLow: number;
  valueHigh: number;
  onChangeLow: (v: number) => void;
  onChangeHigh: (v: number) => void;
}

function DualSlider({ min, max, step, valueLow, valueHigh, onChangeLow, onChangeHigh }: DualSliderProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const range = max - min || 1;
  const leftPct = ((valueLow - min) / range) * 100;
  const rightPct = ((valueHigh - min) / range) * 100;

  return (
    <div className="relative h-6 select-none" ref={trackRef}>
      {/* Track background */}
      <div className="absolute top-1/2 left-0 right-0 h-1 -translate-y-1/2 rounded-full bg-border-soft" />
      {/* Active range */}
      <div
        className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-primary"
        style={{ left: `${leftPct}%`, width: `${rightPct - leftPct}%` }}
      />
      {/* Low thumb */}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={valueLow}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (v <= valueHigh) onChangeLow(v);
        }}
        className="pointer-events-none absolute inset-0 z-10 h-full w-full appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:cursor-grab [&::-webkit-slider-thumb]:active:cursor-grabbing [&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:appearance-none [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-none [&::-moz-range-thumb]:bg-primary [&::-moz-range-thumb]:shadow-md [&::-moz-range-thumb]:cursor-grab [&::-moz-range-thumb]:active:cursor-grabbing"
      />
      {/* High thumb */}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={valueHigh}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (v >= valueLow) onChangeHigh(v);
        }}
        className="pointer-events-none absolute inset-0 z-20 h-full w-full appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:cursor-grab [&::-webkit-slider-thumb]:active:cursor-grabbing [&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:appearance-none [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-none [&::-moz-range-thumb]:bg-primary [&::-moz-range-thumb]:shadow-md [&::-moz-range-thumb]:cursor-grab [&::-moz-range-thumb]:active:cursor-grabbing"
      />
    </div>
  );
}

// ── Histogram ──────────────────────────────────────────────────────

interface HistogramProps {
  buckets: SalaryBucket[];
  lowEur: number;
  highEur: number;
}

function Histogram({ buckets, lowEur, highEur }: HistogramProps) {
  const maxCount = Math.max(...buckets.map((b) => b.count), 1);

  // Prepare all 30 buckets (fill in zeros for missing ones)
  const allBuckets = useMemo(() => {
    const map = new Map(buckets.map((b) => [b.min, b.count]));
    return Array.from({ length: NUM_BUCKETS }, (_, i) => ({
      min: i * BUCKET_WIDTH,
      max: (i + 1) * BUCKET_WIDTH,
      count: map.get(i * BUCKET_WIDTH) ?? 0,
    }));
  }, [buckets]);

  return (
    <div className="flex h-20 items-end gap-px">
      {allBuckets.map((b) => {
        const pct = (b.count / maxCount) * 100;
        const inRange = b.max > lowEur && b.min < highEur;
        return (
          <div
            key={b.min}
            className="flex-1 rounded-t-sm transition-colors"
            style={{ height: `${Math.max(pct, 2)}%` }}
          >
            <div
              className={`h-full w-full rounded-t-sm transition-colors ${
                inRange ? "bg-primary/60" : "bg-border-soft"
              }`}
            />
          </div>
        );
      })}
    </div>
  );
}

// ── Modal ──────────────────────────────────────────────────────────

export function SalaryModal({
  open,
  onOpenChange,
  currency: initialCurrency,
  min: initialMin,
  max: initialMax,
  onApply,
  histogramFilters,
}: SalaryModalProps) {
  const { t } = useLingui();
  const salaryDisplay = useSalaryDisplay();

  // Data — prefer context rates, fetch only histogram
  const contextRates = salaryDisplay.rates;
  const [localRates, setLocalRates] = useState<CurrencyRate[]>([]);
  const rates = contextRates.length > 0 ? contextRates : localRates;
  const [histogram, setHistogram] = useState<SalaryBucket[]>([]);
  const [loading, setLoading] = useState(false);

  // Local state (only applied on close/apply)
  const preferredCurrency = salaryDisplay.displayCurrency ?? initialCurrency;
  const [currency, setCurrency] = useState(preferredCurrency);
  const [lowEur, setLowEur] = useState(0);
  const [highEur, setHighEur] = useState(MAX_EUR);

  const rate = useMemo(
    () => rates.find((r) => r.currency === currency)?.toEur ?? 1,
    [rates, currency],
  );

  const currencies = useMemo(() => rates.map((r) => r.currency).sort(), [rates]);

  // Sync initial props when modal opens
  useEffect(() => {
    if (open) {
      setCurrency(initialCurrency || preferredCurrency);
      if (initialMin != null) {
        setLowEur(toEurVal(initialMin, rates.find((r) => r.currency === initialCurrency)?.toEur ?? 1));
      } else {
        setLowEur(0);
      }
      if (initialMax != null) {
        setHighEur(toEurVal(initialMax, rates.find((r) => r.currency === initialCurrency)?.toEur ?? 1));
      } else {
        setHighEur(MAX_EUR);
      }
    }
  }, [open]);

  // Stable key for histogram filters to detect changes
  const filtersKey = useMemo(() => JSON.stringify(histogramFilters ?? {}), [histogramFilters]);

  // Fetch histogram (and rates if needed) when modal opens or filters change
  const prevFiltersKeyRef = useRef(filtersKey);
  const histogramLoaded = useRef(false);
  useEffect(() => {
    if (!open) return;
    const filtersChanged = prevFiltersKeyRef.current !== filtersKey;
    prevFiltersKeyRef.current = filtersKey;
    if (!histogramLoaded.current || filtersChanged) {
      histogramLoaded.current = true;
      setLoading(true);
      Promise.all([
        rates.length === 0 ? getCurrencyRates() : Promise.resolve(rates),
        getSalaryHistogram(histogramFilters),
      ])
        .then(([r, h]) => { if (r !== rates) setLocalRates(r); setHistogram(h); })
        .finally(() => setLoading(false));
    }
  }, [open, filtersKey]);

  const displayLow = fromEur(lowEur, rate);
  const displayHigh = fromEur(highEur, rate);
  const displayMax = fromEur(MAX_EUR, rate);

  const handleApply = useCallback(() => {
    const minVal = lowEur > 0 ? fromEur(lowEur, rate) : undefined;
    const maxVal = highEur < MAX_EUR ? fromEur(highEur, rate) : undefined;
    onApply(currency, minVal, maxVal);
    onOpenChange(false);
  }, [lowEur, highEur, currency, rate, onApply, onOpenChange]);

  const handleReset = useCallback(() => {
    setLowEur(0);
    setHighEur(MAX_EUR);
  }, []);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans id="search.salaryModal.title" comment="Title for salary filter modal">
                Salary range
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer"
                aria-label={t({ id: "search.salaryModal.close", comment: "Aria label for the salary modal close button", message: "Close" })}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <div className="flex-1 px-5 py-5">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : (
              <div className="space-y-5">
                {/* Currency selector + range labels */}
                <div className="flex items-center justify-between">
                  <select
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value)}
                    className="rounded-md border border-border-soft bg-surface px-2.5 py-1.5 text-sm cursor-pointer"
                  >
                    {currencies.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                  <span className="text-sm font-medium text-foreground">
                    {lowEur > 0 ? formatK(displayLow) : "0"}
                    {" – "}
                    {highEur < MAX_EUR ? formatK(displayHigh) : `${formatK(displayMax)}+`}
                  </span>
                </div>

                {/* Histogram */}
                <Histogram buckets={histogram} lowEur={lowEur} highEur={highEur} />

                {/* Dual slider */}
                <DualSlider
                  min={0}
                  max={MAX_EUR}
                  step={BUCKET_WIDTH}
                  valueLow={lowEur}
                  valueHigh={highEur}
                  onChangeLow={setLowEur}
                  onChangeHigh={setHighEur}
                />

                {/* Axis labels */}
                <div className="flex justify-between text-[11px] text-muted">
                  <span>0</span>
                  <span>{formatK(fromEur(100000, rate))}</span>
                  <span>{formatK(fromEur(200000, rate))}</span>
                  <span>{formatK(fromEur(MAX_EUR, rate))}+</span>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between border-t border-divider px-5 py-4">
            <button
              onClick={handleReset}
              className="cursor-pointer text-sm text-muted transition-colors hover:text-foreground"
            >
              <Trans id="search.salaryModal.reset" comment="Reset salary filter">Reset</Trans>
            </button>
            <button
              onClick={handleApply}
              className="cursor-pointer rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-contrast transition-colors hover:bg-primary/90"
            >
              <Trans id="search.salaryModal.apply" comment="Apply salary filter">Apply</Trans>
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
