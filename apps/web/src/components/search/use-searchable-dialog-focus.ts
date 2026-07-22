"use client";

import { useCallback, useRef } from "react";

/** Focus the primary search input when a searchable Radix dialog opens. */
export function useSearchableDialogFocus() {
  const searchInputRef = useRef<HTMLInputElement>(null);

  const focusSearchInputOnOpen = useCallback((event: Event) => {
    const input = searchInputRef.current;
    if (!input) return;
    event.preventDefault();
    input.focus();
  }, []);

  return { searchInputRef, focusSearchInputOnOpen };
}
