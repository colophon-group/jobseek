"use client";

import { useSession } from "@/components/SessionProvider";

/**
 * Returns the current session from the server-provided SessionProvider.
 *
 * Unlike the previous implementation (which called `authClient.useSession()`
 * triggering `GET /api/auth/get-session` on mount and on window focus),
 * this reads from React context populated during SSR — zero network requests.
 */
export function useAuth() {
  const { user, isLoggedIn } = useSession();
  return { isLoggedIn, user, isPending: false };
}
