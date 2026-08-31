"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";

export function useWatchlistLimitModal() {
  const [open, setOpen] = useState(false);

  return {
    open,
    setOpen,
    show: () => setOpen(true),
  };
}

export function WatchlistLimitModal({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useLingui();

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
          <div className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-warning-bg">
              <AlertTriangle size={18} className="text-warning" aria-hidden="true" />
            </div>
            <div className="flex-1">
              <Dialog.Title className="text-base font-semibold">
                <Trans id="watchlists.limit.title" comment="Title of the neutral notice shown when an account has 10 watchlists">
                  10-watchlist limit
                </Trans>
              </Dialog.Title>
              <Dialog.Description className="mt-1.5 text-sm text-muted">
                <Trans id="watchlists.limit.description" comment="Explanation shown when any watchlist creation path reaches the universal account limit">
                  You can have up to 10 watchlists. Delete one before adding another.
                </Trans>
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="cursor-pointer rounded-md p-1 text-muted transition-colors hover:bg-border-soft hover:text-foreground"
                aria-label={t({ id: "watchlists.limit.close", comment: "Aria label for the watchlist-limit notice close button", message: "Close" })}
              >
                <X size={14} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>

          <div className="mt-5 flex justify-end">
            <Dialog.Close asChild>
              <button
                type="button"
                className="cursor-pointer rounded-md border border-border-soft px-4 py-2 text-sm font-medium transition-colors hover:bg-border-soft"
              >
                <Trans id="watchlists.limit.dismiss" comment="Dismiss the watchlist-limit notice">
                  Got it
                </Trans>
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
