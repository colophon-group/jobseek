import type { ThemeOptions } from "@mui/material/styles";
import { createTheme } from "@mui/material/styles";
import { tokens } from "./tokens";

/**
 * MUI theme — one per color scheme (light / dark).
 *
 * We deliberately avoid MUI's CSS-variables mode (`cssVariables`)
 * because its `CssVarsProvider` manages the `.dark` class on <html>
 * via localStorage / system preference, conflicting with our
 * cookie-based SSR approach.  Instead we build two static themes
 * and swap them in ThemeProvider based on the React state.
 *
 * Token hex values come from tokens.ts (shared with globals.css).
 */

const typography: ThemeOptions["typography"] = {
  fontFamily: "var(--font-sans), 'JetBrains Mono', 'Inter', 'Helvetica Neue', 'Arial', sans-serif",
  fontWeightRegular: 500,
  fontWeightMedium: 600,
  fontWeightBold: 700,
  body1: { fontWeight: 500 },
  body2: { fontWeight: 500 },
  button: { fontWeight: 600, letterSpacing: 0.2 },
  h1: { fontWeight: 700 },
  h2: { fontWeight: 700 },
  h3: { fontWeight: 700 },
  h4: { fontWeight: 700 },
  h5: { fontWeight: 600 },
  h6: { fontWeight: 600 },
};

const shape = { borderRadius: 12 };

const components: ThemeOptions["components"] = {
  MuiButton: {
    styleOverrides: {
      root: {
        textTransform: "none",
        fontWeight: 600,
        borderRadius: 999,
        border: "1px solid currentColor",
      },
    },
  },
  MuiCard: {
    styleOverrides: {
      root: {
        borderRadius: 20,
        border: "1px solid",
        borderColor: "var(--border-soft)",
      },
    },
  },
  MuiIconButton: {
    styleOverrides: {
      root: {
        borderRadius: 12,
      },
    },
  },
};

function paletteFrom(t: (typeof tokens)["light" | "dark"]) {
  return {
    background: { default: t.background, paper: t.surface },
    text: { primary: t.foreground, secondary: t.muted },
    primary: { main: t.primary, contrastText: t.primaryContrast },
    secondary: { main: t.secondary, contrastText: t.secondaryContrast },
    divider: t.divider,
    info: { main: t.info },
    success: { main: t.success },
    warning: { main: t.warning },
    error: { main: t.error },
  };
}

export function buildMuiTheme(mode: "light" | "dark") {
  return createTheme({
    palette: {
      mode,
      ...paletteFrom(tokens[mode]),
    },
    typography,
    shape,
    components,
  });
}
