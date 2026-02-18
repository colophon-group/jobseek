"use client";

import { useEffect, useState } from "react";
import { CssBaseline, ThemeProvider as MuiThemeProvider } from "@mui/material";
import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { buildMuiTheme } from "@/theme/createMuiTheme";

type ThemeMode = "light" | "dark";

const muiTheme = buildMuiTheme();

export function ThemeProvider({
  children,
  initialTheme = "dark",
}: {
  children: React.ReactNode;
  initialTheme?: ThemeMode;
}) {
  const [mode, setMode] = useState<ThemeMode>(initialTheme);

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") {
      setMode(saved);
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", mode === "dark");
    localStorage.setItem("theme", mode);
    document.cookie = `theme=${mode}; path=/; max-age=31536000`;
  }, [mode]);

  return (
    <ThemeContext.Provider value={{ mode, setMode }}>
      <AppRouterCacheProvider>
        <MuiThemeProvider theme={muiTheme}>
          <CssBaseline enableColorScheme />
          {children}
        </MuiThemeProvider>
      </AppRouterCacheProvider>
    </ThemeContext.Provider>
  );
}

import { createContext } from "react";

export const ThemeContext = createContext<{
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
}>({
  mode: "dark",
  setMode: () => {},
});
