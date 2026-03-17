"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import {
  getExperienceHistogram,
  type ExperienceBucket,
} from "@/lib/actions/search";
import type { HistogramFilters } from "@/lib/search";

const MAX_YEARS = 15;

interface ExperienceModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  min: number | undefined;
  max: number | undefined;
  onApply: (min: number | undefined, max: number | undefined) => void;
  histogramFilters?: HistogramFilters;
}

// ── Histogram ──────────────────────────────────────────────────────

interface HistogramProps {
  buckets: ExperienceBucket[];
  low: number;
  high: number;
}

function Histogram({ buckets, low, high }: HistogramProps) {
  const bucketMap = useMemo(
    () => new Map(buckets.map((b) => [b.years, b.count])),
    [buckets],
  );
  const maxCount = Math.max(...buckets.map((b) => b.count), 1);

  return (
    <div className="flex h-20 items-end gap-1">
      {Array.from({ length: MAX_YEARS + 1 }, (_, y) => {
        const count = bucketMap.get(y) ?? 0;
        const pct = (count / maxCount) * 100;
        const inRange = y >= low && y <= high;
        return (
          <div key={y} className="flex flex-1 flex-col items-center gap-1">
            <div className="relative w-full" style={{ height: 80 }}>
              <div
                className={`absolute bottom-0 w-full rounded-t-sm transition-colors ${
                  inRange ? "bg-primary/60" : "bg-border-soft"
                }`}
                style={{ height: `${Math.max(pct, 3)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Dual range slider ──────────────────────────────────────────────

interface DualSliderProps {
  min: number;
  max: number;
  valueLow: number;
  valueHigh: number;
  onChangeLow: (v: number) => void;
  onChangeHigh: (v: number) => void;
}

function DualSlider({ min, max, valueLow, valueHigh, onChangeLow, onChangeHigh }: DualSliderProps) {
  const range = max - min || 1;
  const leftPct = ((valueLow - min) / range) * 100;
  const rightPct = ((valueHigh - min) / range) * 100;

  return (
    <div className="relative h-6 select-none">
      <div className="absolute top-1/2 left-0 right-0 h-1 -translate-y-1/2 rounded-full bg-border-soft" />
      <div
        className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-primary"
        style={{ left: `${leftPct}%`, width: `${rightPct - leftPct}%` }}
      />
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={valueLow}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (v <= valueHigh) onChangeLow(v);
        }}
        className="pointer-events-none absolute inset-0 z-10 h-full w-full appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:cursor-grab [&::-webkit-slider-thumb]:active:cursor-grabbing [&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:appearance-none [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-none [&::-moz-range-thumb]:bg-primary [&::-moz-range-thumb]:shadow-md [&::-moz-range-thumb]:cursor-grab [&::-moz-range-thumb]:active:cursor-grabbing"
      />
      <input
        type="range"
        min={min}
        max={max}
        step={1}
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

// ── Modal ──────────────────────────────────────────────────────────

export function ExperienceModal({
  open,
  onOpenChange,
  min: initialMin,
  max: initialMax,
  onApply,
  histogramFilters,
}: ExperienceModalProps) {
  const { t } = useLingui();

  const [histogram, setHistogram] = useState<ExperienceBucket[]>([]);
  const [loading, setLoading] = useState(false);

  const [low, setLow] = useState(0);
  const [high, setHigh] = useState(MAX_YEARS);

  // Sync when modal opens
  useEffect(() => {
    if (open) {
      setLow(initialMin ?? 0);
      setHigh(initialMax ?? MAX_YEARS);
    }
  }, [open]);

  // Stable key for histogram filters to detect changes
  const filtersKey = useMemo(() => JSON.stringify(histogramFilters ?? {}), [histogramFilters]);

  // Fetch data when modal opens or filters change
  const prevFiltersKeyRef = useRef(filtersKey);
  useEffect(() => {
    if (!open) return;
    const filtersChanged = prevFiltersKeyRef.current !== filtersKey;
    prevFiltersKeyRef.current = filtersKey;
    if (histogram.length === 0 || filtersChanged) {
      setLoading(true);
      getExperienceHistogram(histogramFilters)
        .then(setHistogram)
        .finally(() => setLoading(false));
    }
  }, [open, histogram.length, filtersKey]);

  const handleApply = useCallback(() => {
    const minVal = low > 0 ? low : undefined;
    const maxVal = high < MAX_YEARS ? high : undefined;
    onApply(minVal, maxVal);
    onOpenChange(false);
  }, [low, high, onApply, onOpenChange]);

  const handleReset = useCallback(() => {
    setLow(0);
    setHigh(MAX_YEARS);
  }, []);

  const rangeLabel = useMemo(() => {
    if (low === 0 && high === MAX_YEARS) return t({ id: "search.experienceModal.any", comment: "Any experience level", message: "Any" });
    if (low === 0) return `0 – ${high} ${t({ id: "search.experienceModal.years", comment: "Years abbreviation", message: "years" })}`;
    if (high === MAX_YEARS) return `${low}+ ${t({ id: "search.experienceModal.years", comment: "Years abbreviation", message: "years" })}`;
    return `${low} – ${high} ${t({ id: "search.experienceModal.years", comment: "Years abbreviation", message: "years" })}`;
  }, [low, high, t]);

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
              <Trans id="search.experienceModal.title" comment="Title for experience filter modal">
                Experience
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={16} />
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
                {/* Range label */}
                <div className="text-center text-sm font-medium text-foreground">
                  {rangeLabel}
                </div>

                {/* Histogram */}
                <Histogram buckets={histogram} low={low} high={high} />

                {/* Dual slider */}
                <DualSlider
                  min={0}
                  max={MAX_YEARS}
                  valueLow={low}
                  valueHigh={high}
                  onChangeLow={setLow}
                  onChangeHigh={setHigh}
                />

                {/* Axis labels */}
                <div className="flex justify-between text-[11px] text-muted">
                  <span>0y</span>
                  <span>5y</span>
                  <span>10y</span>
                  <span>15y+</span>
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
              <Trans id="search.experienceModal.reset" comment="Reset experience filter">Reset</Trans>
            </button>
            <button
              onClick={handleApply}
              className="cursor-pointer rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-contrast transition-colors hover:bg-primary/90"
            >
              <Trans id="search.experienceModal.apply" comment="Apply experience filter">Apply</Trans>
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
