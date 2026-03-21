"use client";

import { useMemo } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react";
import { t } from "@lingui/core/macro";

function useEmploymentTypes() {
  useLingui();
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
}

export function EmploymentTypeModal({
  open,
  onOpenChange,
  selected,
  onToggle,
}: EmploymentTypeModalProps) {
  const EMPLOYMENT_TYPES = useEmploymentTypes();
  const selectedSet = useMemo(() => new Set(selected), [selected]);

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
              <button className="cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="flex flex-col gap-2">
              {EMPLOYMENT_TYPES.map((opt) => {
                const active = selectedSet.has(opt.value);
                return (
                  <button
                    key={opt.value}
                    onClick={() => onToggle(opt.value)}
                    className={`flex cursor-pointer items-center rounded-lg px-4 py-3 text-sm font-medium transition-colors ${
                      active
                        ? "bg-primary/10 text-primary"
                        : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
