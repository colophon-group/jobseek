"use client";

import { useEffect, useRef } from "react";
import { clearTypesenseBrowserConfig } from "./typesense-browser-key";

/**
 * Clears the cached browser-side Typesense scoped key on auth-state
 * transitions. Skips first-mount: clearing on every page mount would
 * waste a /api/typesense-key fetch on every soft navigation.
 *
 * Safe to call from any client page; no-ops unless NEXT_PUBLIC_TYPESENSE_DIRECT=1.
 */
export function useClearTypesenseOnAuthChange(isLoggedIn: boolean): void {
  const previous = useRef(isLoggedIn);
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_TYPESENSE_DIRECT !== "1") return;
    if (previous.current === isLoggedIn) return;
    previous.current = isLoggedIn;
    clearTypesenseBrowserConfig();
  }, [isLoggedIn]);
}
