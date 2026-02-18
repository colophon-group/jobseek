"use client";

import { createContext, useEffect } from "react";
import { CssBaseline, ThemeProvider as MuiThemeProvider } from "@mui/material";
import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { useColorScheme } from "@mui/material/styles";
import { buildMuiTheme } from "@/theme/createMuiTheme";

type ThemeMode = "light" | "dark";

const muiTheme = buildMuiTheme();

export const ThemeContext = createContext<{
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
}>({
  mode: "dark",
  setMode: () => {},
});

/**
 * Bridges MUI's useColorScheme with our ThemeContext and syncs
 * the cookie so the server layout can read the preference.
 */
function ThemeController({
  initialTheme,
  children,
}: {
  initialTheme: ThemeMode;
  children: React.ReactNode;
}) {
  const { mode, setMode } = useColorScheme();

  // During SSR useColorScheme returns undefined; fall back to the
  // server-determined value so the first render matches.
  const resolved: ThemeMode =
    mode === "light" || mode === "dark" ? mode : initialTheme;

  // colorSchemeSelector: ".dark" only affects CSS generation — MUI does
  // not toggle the class itself, so we sync it from the resolved mode.
  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolved === "dark");
    document.cookie = `theme=${resolved}; path=/; max-age=31536000`;
  }, [resolved]);

  return (
    <ThemeContext.Provider value={{ mode: resolved, setMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function ThemeProvider({
  children,
  initialTheme = "dark",
}: {
  children: React.ReactNode;
  initialTheme?: ThemeMode;
}) {
  return (
    <AppRouterCacheProvider>
      <MuiThemeProvider
        theme={muiTheme}
        defaultMode={initialTheme}
        modeStorageKey="theme"
      >
        <CssBaseline enableColorScheme />
        <ThemeController initialTheme={initialTheme}>
          {children}
        </ThemeController>
      </MuiThemeProvider>
    </AppRouterCacheProvider>
  );
}
