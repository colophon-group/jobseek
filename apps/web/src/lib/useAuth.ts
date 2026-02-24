"use client";

import { useState, useEffect } from "react";
import { authClient } from "@/lib/auth-client";

const CACHE_KEY = "auth-user";

/**
 * Thin convenience hook over Better Auth's `useSession()`.
 *
 * Caches the user in localStorage so subsequent page loads
 * show the last-known state (after hydration) while the real
 * session fetch runs in the background.
 */
export function useAuth() {
  const { data: session, isPending } = authClient.useSession();
  const [cached, setCached] = useState<any>(null);

  // Read cache after mount to avoid hydration mismatch
  useEffect(() => {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (raw) setCached(JSON.parse(raw));
    } catch {}
  }, []);

  // Persist or clear cache when session resolves
  useEffect(() => {
    if (isPending) return;
    if (session?.user) {
      localStorage.setItem(CACHE_KEY, JSON.stringify(session.user));
      setCached(session.user);
    } else {
      localStorage.removeItem(CACHE_KEY);
      setCached(null);
    }
  }, [session, isPending]);

  const user = isPending ? cached : (session?.user ?? null);
  return { isLoggedIn: Boolean(user), user, isPending };
}
