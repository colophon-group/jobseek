"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import Link from "next/link";
import { useLocalePath } from "@/lib/useLocalePath";

export function useUpgradeModal() {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");

  function show(message: string) {
    setReason(message);
    setOpen(true);
  }

  return { open, setOpen, reason, show };
}

export function UpgradeModal({
  open,
  onOpenChange,
  reason,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  reason: string;
}) {
  const { t } = useLingui();
  const lp = useLocalePath();

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          <div className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-warning-bg">
              <AlertTriangle size={18} className="text-warning" />
            </div>
            <div className="flex-1">
              <Dialog.Title className="text-base font-semibold">
                <Trans id="upgrade.modal.title" comment="Upgrade modal title">
                  Upgrade required
                </Trans>
              </Dialog.Title>
              <p className="mt-1.5 text-sm text-muted">
                {reason}
              </p>
            </div>
            <Dialog.Close asChild>
              <button className="rounded-md p-1 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={14} />
              </button>
            </Dialog.Close>
          </div>

          <div className="mt-5 flex justify-end gap-2">
            <Dialog.Close asChild>
              <button className="rounded-md border border-border-soft px-4 py-2 text-sm font-medium transition-colors hover:bg-border-soft cursor-pointer">
                {t({ id: "upgrade.modal.dismiss", comment: "Dismiss upgrade modal", message: "Got it" })}
              </button>
            </Dialog.Close>
            <Link
              href={lp("/settings/billing")}
              onClick={() => onOpenChange(false)}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-contrast transition-opacity hover:opacity-90"
            >
              <Trans id="upgrade.modal.upgrade" comment="Upgrade button in modal">
                Upgrade
              </Trans>
            </Link>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
