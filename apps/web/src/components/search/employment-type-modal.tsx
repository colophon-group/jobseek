"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { getEmploymentTypeCounts } from "@/lib/actions/taxonomy";

function useEmploymentTypes() {
  const { t } = useLingui();
  return [
    { value: "full_time", label: t({ id: "search.employmentType.fullTime", comment: "Employment type: full-time", message: "Full-time" }) },
    { value: "part_time", label: t({ id: "search.employmentType.partTime", comment: "Employment type: part-time", message: "Part-time" }) },
    { value: "contract", label: t({ id: "search.employmentType.contract", comment: "Employment type: contract", message: "Contract" }) },
    { value: "internship", label: t({ id: "search.employmentType.internship", comment: "Employment type: internship", message: "Internship" }) },
    { value: "temporary", label: t({ id: "search.employmentType.temporary", comment: "Employment type: temporary", message: "Temporary" }) },
    { value: "volunteer", label: t({ id: "search.employmentType.volunteer", comment: "Employment type: volunteer", message: "Volunteer" }) },
  ] as const;
}

interface EmploymentTypeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: string[];
  onToggle: (type: string) => void;
  /**
   * Cross-filter context used to compute per-option counts via Typesense
   * facets — mirrors the seniority/technology modal pattern. Pass the
   * currently-applied filter set (minus `employmentTypes`, since that's
   * the dimension being faceted) so counts reflect what the user would
   * see if they toggled that option. Optional: when omitted, counts are
   * fetched against the unfiltered active-postings universe. See #3032.
   */
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; workMode?: string[]; languages?: string[] };
}

export function EmploymentTypeModal({
  open,
  onOpenChange,
  selected,
  onToggle,
  filters,
}: EmploymentTypeModalProps) {
  const { t } = useLingui();
  const EMPLOYMENT_TYPES = useEmploymentTypes();
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [loading, setLoading] = useState(false);
  const filtersKey = filters ? JSON.stringify(filters) : "";
  const prevFiltersKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (counts !== null && filtersKey === prevFiltersKeyRef.current) return;
    prevFiltersKeyRef.current = filtersKey;
    setLoading(true);
    getEmploymentTypeCounts(filters)
      .then(setCounts)
      .finally(() => setLoading(false));
  }, [open, filtersKey]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans id="search.employmentTypeModal.title" comment="Title for the employment type selection modal">
                Employment type
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground"
                aria-label={t({ id: "search.employmentTypeModal.close", comment: "Aria label for the employment-type modal close button", message: "Close" })}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4">
            {loading && counts === null ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {EMPLOYMENT_TYPES.map((opt) => {
                  const active = selectedSet.has(opt.value);
                  const count = counts?.[opt.value] ?? 0;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => onToggle(opt.value)}
                      className={`flex cursor-pointer items-center justify-between rounded-lg px-4 py-3 text-sm font-medium transition-colors ${
                        active
                          ? "bg-primary/10 text-primary"
                          : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                      }`}
                    >
                      <span>{opt.label}</span>
                      <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                        ({count})
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
