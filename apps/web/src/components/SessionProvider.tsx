"use client";

import { createContext, useContext, type ReactNode } from "react";

export type SessionUser = {
  id: string;
  email: string;
  name: string;
  image?: string | null;
  emailVerified: boolean;
  username?: string | null;
  displayUsername?: string | null;
};

type SessionContextValue = {
  user: SessionUser | null;
  isLoggedIn: boolean;
  isPending: boolean;
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
  isLoggedIn: false,
  isPending: true,
  refresh: async () => {},
});

export function SessionProvider({
  user,
  isPending = false,
  refresh,
  children,
}: {
  user: SessionUser | null;
  isPending?: boolean;
  refresh?: () => Promise<void>;
  children: ReactNode;
}) {
  return (
    <SessionContext.Provider
      value={{
        user,
        isLoggedIn: Boolean(user),
        isPending,
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
