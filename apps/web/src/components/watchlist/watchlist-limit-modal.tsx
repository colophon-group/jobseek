"use client";

import { useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";

type FocusTarget = HTMLElement | (() => HTMLElement | null) | null;

function resolveFocusTarget(target: FocusTarget): HTMLElement | null {
  return typeof target === "function" ? target() : target;
}

function focusIfAvailable(target: FocusTarget): boolean {
  const element = resolveFocusTarget(target);
  if (
    !element?.isConnected
    || element.matches(":disabled, [aria-disabled='true']")
    || element.closest("[inert]")
  ) {
    return false;
  }

  element.focus({ preventScroll: true });
  return document.activeElement === element;
}

export function useWatchlistLimitModal() {
  const [open, setOpen] = useState(false);
  const returnFocusRef = useRef<FocusTarget>(null);
  const fallbackFocusRef = useRef<FocusTarget>(null);

  function show(returnFocus?: FocusTarget, fallbackFocus: FocusTarget = null) {
    returnFocusRef.current = returnFocus === undefined
      ? document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
      : returnFocus;
    fallbackFocusRef.current = fallbackFocus;
    setOpen(true);
  }

  function restoreFocus() {
    if (!focusIfAvailable(returnFocusRef.current)) {
      focusIfAvailable(fallbackFocusRef.current);
    }
    returnFocusRef.current = null;
    fallbackFocusRef.current = null;
  }

  return {
    open,
    setOpen,
    show,
    restoreFocus,
  };
}

export function WatchlistLimitModal({
  open,
  onOpenChange,
  onCloseAutoFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus: () => void;
}) {
  const { t } = useLingui();

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            onCloseAutoFocus();
          }}
        >
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
