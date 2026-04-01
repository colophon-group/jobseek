"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Loader2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { getAllSeniorities } from "@/lib/actions/taxonomy";
import type { SeniorityOption } from "@/lib/actions/taxonomy";

interface SeniorityModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  locale: string;
  selected: { id: number; slug: string; name: string }[];
  onToggle: (sen: { id: number; slug: string; name: string }) => void;
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; technologyIds?: number[]; languages?: string[] };
}

export function SeniorityModal({
  open,
  onOpenChange,
  locale,
  selected,
  onToggle,
  filters,
}: SeniorityModalProps) {
  const [options, setOptions] = useState<SeniorityOption[] | null>(null);
  const [loading, setLoading] = useState(false);

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  const filtersKey = filters ? JSON.stringify(filters) : "";
  const prevFiltersKeyRef = useRef(filtersKey);

  useEffect(() => {
    if (open && (!options || filtersKey !== prevFiltersKeyRef.current)) {
      prevFiltersKeyRef.current = filtersKey;
      setLoading(true);
      getAllSeniorities(locale, filters)
        .then(setOptions)
        .finally(() => setLoading(false));
    }
  }, [open, options, locale, filtersKey]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans id="search.seniorityModal.title" comment="Title for the seniority level selection modal">
                Select level
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="h-full px-5 py-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : !options || options.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="search.seniorityModal.noResults" comment="No seniority levels available">
                  No seniority levels available.
                </Trans>
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {options.map((opt) => {
                  const active = selectedIds.has(opt.id);
                  return (
                    <button
                      key={opt.id}
                      onClick={() => onToggle({ id: opt.id, slug: opt.slug, name: opt.name })}
                      className={`flex cursor-pointer items-center justify-between rounded-lg px-4 py-3 text-sm transition-colors ${
                        active
                          ? "bg-primary/10 text-primary"
                          : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                      }`}
                    >
                      <span className="font-medium">{opt.name}</span>
                      <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
                        ({opt.count})
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
