"use client";

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

type AuthState = {
  isLoggedIn: boolean;
};

const AuthContext = createContext<AuthState>({ isLoggedIn: false });

export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthContext.Provider value={{ isLoggedIn: false }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
