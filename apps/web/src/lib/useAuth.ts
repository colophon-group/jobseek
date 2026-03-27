"use client";

import { useSession } from "@/components/SessionProvider";

/**
 * Returns the current session from the SessionProvider context.
 *
 * `isPending` is true while the initial bootstrap fetch is in progress.
 * Components should show neutral/skeleton UI while isPending to avoid
 * flashing auth-dependent content.
 */
export function useAuth() {
  const { user, isLoggedIn, isPending } = useSession();
  return { isLoggedIn, user, isPending };
}
