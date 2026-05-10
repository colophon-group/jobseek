"use client";

import { useMemo } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { ScrollFade } from "@/components/ui/scroll-fade";
import type { WorkMode } from "@/lib/search/types";

/**
 * Work-mode (location_types) modal — mirrors employment-type-modal.tsx
 * exactly. Multi-select via `selected: WorkMode[]` + `onToggle`. Three
 * fixed options sourced from {@link WORK_MODE_VALUES}; we hard-code the
 * order here (onsite → hybrid → remote) so the modal layout is stable.
 * Issue #2983.
 */
function useWorkModes() {
  const { t } = useLingui();
  return [
    { value: "onsite" as const, label: t({ id: "search.workMode.onsite", comment: "Work mode: onsite (in-office)", message: "On-site" }) },
    { value: "hybrid" as const, label: t({ id: "search.workMode.hybrid", comment: "Work mode: hybrid (mixed onsite/remote)", message: "Hybrid" }) },
    { value: "remote" as const, label: t({ id: "search.workMode.remote", comment: "Work mode: remote (work-from-home)", message: "Remote" }) },
  ] as const;
}

interface WorkModeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: WorkMode[];
  onToggle: (mode: WorkMode) => void;
}

export function WorkModeModal({
  open,
  onOpenChange,
  selected,
  onToggle,
}: WorkModeModalProps) {
  const WORK_MODES = useWorkModes();
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
              <Trans id="search.workModeModal.title" comment="Title for the work-mode (onsite/hybrid/remote) selection modal">
                Work mode
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4">
            <div className="flex flex-col gap-2">
              {WORK_MODES.map((opt) => {
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
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
