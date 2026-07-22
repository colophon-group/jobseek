"use client";

import { useCallback, useRef } from "react";

/** Focus the primary search input when a searchable Radix dialog opens. */
export function useSearchableDialogFocus() {
  const searchInputRef = useRef<HTMLInputElement>(null);
  const restoreFocusElementRef = useRef<HTMLElement | null>(null);

  const focusSearchInputOnOpen = useCallback((event: Event) => {
    if (typeof document !== "undefined") {
      const activeElement = document.activeElement;
      restoreFocusElementRef.current =
        activeElement instanceof HTMLElement && activeElement !== document.body
          ? activeElement
          : null;
    }

    const input = searchInputRef.current;
    if (!input) return;
    event.preventDefault();
    input.focus();
  }, []);

  const restoreTriggerFocusOnClose = useCallback((event: Event) => {
    const restoreTarget = restoreFocusElementRef.current;
    restoreFocusElementRef.current = null;
    if (!restoreTarget?.isConnected) return;

    event.preventDefault();
    restoreTarget.focus();
  }, []);

  return {
    searchInputRef,
    focusSearchInputOnOpen,
    restoreTriggerFocusOnClose,
  };
}
