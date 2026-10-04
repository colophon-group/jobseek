"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { AppPreferences } from "@/lib/actions/bootstrap";
import type { PlanId } from "@/lib/plans";

export type SessionUser = {
  id: string;
  email: string;
  name: string;
  image?: string | null;
  emailVerified: boolean;
  username?: string | null;
  displayUsername?: string | null;
};

export type AccountStatus = "pending" | "ready" | "unavailable";

type SessionContextValue = {
  user: SessionUser | null;
  plan: PlanId;
  preferences: AppPreferences | null;
  isLoggedIn: boolean;
  /** Identity is unresolved; consumers must not use anonymous write/import paths. */
  isPending: boolean;
  accountStatus: AccountStatus;
  isRefreshing: boolean;
  canRetry: boolean;
  retry: () => Promise<void>;
  invalidate: () => void;
  /**
   * Re-fetch the session payload from the server and update the
   * SessionProvider state in place. Use after a server-side mutation
   * that changes the viewer's identity (e.g. `renameUsername` from
   * `actions/preferences.ts`) so that client components reading
   * `user.username` rebuild URLs from the fresh value rather than the
   * one bootstrapped on the initial mount. See issue #3022.
   *
   * The default no-op is here for stand-alone test mounts that don't
   * wrap children in `AppBootstrapProvider`; the production tree
   * always supplies a real implementation.
   */
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue>({
  user: null,
  plan: "free",
  preferences: null,
  isLoggedIn: false,
  isPending: true,
  accountStatus: "pending",
  isRefreshing: false,
  canRetry: false,
  retry: async () => {},
  invalidate: () => {},
  refresh: async () => {},
});

export function SessionProvider({
  user,
  plan = "free",
  preferences = null,
  isPending = false,
  refresh,
  accountStatus = isPending ? "pending" : "ready",
  isRefreshing = false,
  canRetry = false,
  retry,
  invalidate,
  children,
}: {
  user: SessionUser | null;
  plan?: PlanId;
  preferences?: AppPreferences | null;
  isPending?: boolean;
  refresh?: () => Promise<void>;
  accountStatus?: AccountStatus;
  isRefreshing?: boolean;
  canRetry?: boolean;
  retry?: () => Promise<void>;
  invalidate?: () => void;
  children: ReactNode;
}) {
  return (
    <SessionContext.Provider
      value={{
        user,
        plan,
        preferences,
        isLoggedIn: Boolean(user),
        isPending,
        accountStatus,
        isRefreshing,
        canRetry,
        retry: retry ?? (async () => {}),
        invalidate: invalidate ?? (() => {}),
        refresh: refresh ?? (async () => {}),
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  return useContext(SessionContext);
}
