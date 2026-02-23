"use client";

import { authClient } from "@/lib/auth-client";

/**
 * Thin convenience hook over Better Auth's `useSession()`.
 *
 * Keeps existing call-sites (`useAuth().isLoggedIn`) working
 * without coupling every component directly to `better-auth`.
 */
export function useAuth() {
  const { data: session } = authClient.useSession();
  return { isLoggedIn: Boolean(session?.user), user: session?.user ?? null };
}
