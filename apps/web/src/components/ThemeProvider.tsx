"use client";

import { CssBaseline, ThemeProvider as MuiBaseThemeProvider } from "@mui/material";
import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { muiTheme } from "@/theme/createMuiTheme";

/**
 * MUI wrapper using CSS-variables mode.
 *
 * All MUI palette colors are CSS custom properties (--mui-palette-*)
 * whose values switch when the `.dark` class is present on <html>.
 * next-themes' inline script sets that class before first paint,
 * so colors are always correct — no JS theme swap, no mounted guard.
 */
export function MuiThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <AppRouterCacheProvider>
      <MuiBaseThemeProvider theme={muiTheme}>
        <CssBaseline />
        {children}
      </MuiBaseThemeProvider>
    </AppRouterCacheProvider>
  );
}
