"use client";

import { useEffect } from "react";
import { clearTypesenseBrowserConfig } from "./typesense-browser-key";

/**
 * Clears the cached browser-side Typesense scoped key whenever the auth
 * state flips, so a sign-in/sign-out doesn't keep the wrong key (anon
 * truncation cap vs. authed unrestricted).
 *
 * Safe to call from any client page; no-ops unless NEXT_PUBLIC_TYPESENSE_DIRECT=1.
 */
export function useClearTypesenseOnAuthChange(isLoggedIn: boolean): void {
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_TYPESENSE_DIRECT !== "1") return;
    clearTypesenseBrowserConfig();
  }, [isLoggedIn]);
}
