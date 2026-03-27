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
};

const SessionContext = createContext<SessionContextValue>({
  user: null,
  isLoggedIn: false,
  isPending: true,
});

export function SessionProvider({
  user,
  isPending = false,
  children,
}: {
  user: SessionUser | null;
  isPending?: boolean;
  children: ReactNode;
}) {
  return (
    <SessionContext.Provider value={{ user, isLoggedIn: Boolean(user), isPending }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  return useContext(SessionContext);
}
