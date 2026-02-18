import type { ThemeOptions } from "@mui/material/styles";
import { createTheme } from "@mui/material/styles";
import { tokens } from "./tokens";

/**
 * MUI theme with CSS variables mode + light/dark color schemes.
 *
 * MUI generates `--mui-palette-*` CSS variables automatically.
 * The `.dark` class on <html> triggers the dark scheme — same
 * class that globals.css uses for its custom properties.
 *
 * Token hex values come from tokens.ts (shared with globals.css).
 */

const typography: ThemeOptions["typography"] = {
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

export function buildMuiTheme() {
  return createTheme({
    cssVariables: {
      colorSchemeSelector: ".dark",
    },
    colorSchemes: {
      light: { palette: paletteFrom(tokens.light) },
      dark: { palette: paletteFrom(tokens.dark) },
    },
    typography,
    shape,
    components,
  });
}
