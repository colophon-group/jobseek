"use client";

import { useMemo } from "react";
import { useTheme } from "next-themes";
import { CssBaseline, ThemeProvider as MuiBaseThemeProvider } from "@mui/material";
import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { buildMuiTheme } from "@/theme/createMuiTheme";

const lightTheme = buildMuiTheme("light");
const darkTheme = buildMuiTheme("dark");

/**
 * MUI wrapper that reads the resolved theme from next-themes
 * and passes the corresponding MUI theme to MUI's ThemeProvider.
 */
export function MuiThemeProvider({ children }: { children: React.ReactNode }) {
  const { resolvedTheme } = useTheme();
  const muiTheme = useMemo(
    () => (resolvedTheme === "light" ? lightTheme : darkTheme),
    [resolvedTheme],
  );

  return (
    <AppRouterCacheProvider>
      <MuiBaseThemeProvider theme={muiTheme}>
        <CssBaseline />
        {children}
      </MuiBaseThemeProvider>
    </AppRouterCacheProvider>
  );
}
