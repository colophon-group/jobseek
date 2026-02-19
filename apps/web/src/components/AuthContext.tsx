"use client";

import { useUser } from "@stackframe/stack";

/**
 * Thin convenience hook over Stack's `useUser()`.
 *
 * Keeps existing call-sites (`useAuth().isLoggedIn`) working
 * without coupling every component directly to `@stackframe/stack`.
 */
export function useAuth() {
  const user = useUser();
  return { isLoggedIn: Boolean(user), user };
}
