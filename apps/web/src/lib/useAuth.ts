"use client";

import { useSession } from "@/components/SessionProvider";

/**
 * Returns the current session from the SessionProvider context.
 *
 * `isPending` is true while the initial bootstrap fetch is in progress.
 * Components should show neutral/skeleton UI while isPending to avoid
 * flashing auth-dependent content.
 *
 * `refresh()` re-fetches the session payload from the server — call it
 * after an identity-mutating server action (e.g. `renameUsername`) so
 * downstream URL builders rebuild against the new `user.username`
 * rather than the value bootstrapped on initial mount (#3022).
 */
export function useAuth() {
  const { user, isLoggedIn, isPending, refresh } = useSession();
  return { isLoggedIn, user, isPending, refresh };
}
