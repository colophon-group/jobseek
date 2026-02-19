"use client";

import { createContext, useState, useCallback, useEffect, useMemo } from "react";
import { CssBaseline, ThemeProvider as MuiThemeProvider } from "@mui/material";
import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { buildMuiTheme } from "@/theme/createMuiTheme";

type ThemeMode = "light" | "dark";

const lightTheme = buildMuiTheme("light");
const darkTheme = buildMuiTheme("dark");

/**
 * Module-level cache: survives component remounts during soft navigation
 * (e.g. locale switch) but resets on hard refresh.  This prevents the
 * theme from flickering when Next.js re-initialises the component with
 * a potentially stale `initialTheme` from the Router Cache.
 */
let clientMode: ThemeMode | undefined;

export const ThemeContext = createContext<{
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
}>({
  mode: "dark",
  setMode: () => {},
});

/**
 * Single source of truth for theme mode.
 *
 * We use a cookie so the server can read the preference and set the
 * `.dark` class on <html> at SSR time (no flash).  On the client a
 * `useEffect` keeps the DOM class in sync after hydration — this
 * also handles component remounts triggered by locale-route changes.
 *
 * We do NOT use MUI's CSS-variables mode / `useColorScheme` because
 * its CssVarsProvider manages the `.dark` class independently via
 * localStorage and system-preference detection, which conflicts
 * with the cookie-based SSR approach.
 */
export function ThemeProvider({
  children,
  initialTheme = "dark",
}: {
  children: React.ReactNode;
  initialTheme?: ThemeMode;
}) {
  const [mode, setModeRaw] = useState<ThemeMode>(() => {
    // On remount (soft navigation), prefer the in-memory value
    // so a stale RSC payload can't reset the user's choice.
    if (clientMode !== undefined) return clientMode;
    clientMode = initialTheme;
    return initialTheme;
  });

  const setMode = useCallback((next: ThemeMode) => {
    clientMode = next;
    setModeRaw(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    document.cookie = `theme=${next}; path=/; max-age=31536000`;
  }, []);

  // Keep the DOM `.dark` class in sync with React state.
  // Covers initial hydration (cookie ↔ system-preference mismatch)
  // and component remounts caused by locale-route navigation.
  useEffect(() => {
    document.documentElement.classList.toggle("dark", mode === "dark");
  }, [mode]);

  const muiTheme = useMemo(
    () => (mode === "dark" ? darkTheme : lightTheme),
    [mode],
  );

  return (
    <AppRouterCacheProvider>
      <MuiThemeProvider theme={muiTheme}>
        <CssBaseline enableColorScheme />
        <ThemeContext.Provider value={{ mode, setMode }}>
          {children}
        </ThemeContext.Provider>
      </MuiThemeProvider>
    </AppRouterCacheProvider>
  );
}
