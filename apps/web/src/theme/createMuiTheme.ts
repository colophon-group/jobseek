import type { ThemeOptions } from "@mui/material/styles";
import { createTheme } from "@mui/material/styles";
import { tokens } from "./tokens";

/**
 * Single MUI theme with CSS-variables color schemes.
 *
 * MUI generates CSS custom properties (--mui-palette-*) for every palette
 * token.  The dark scheme is activated by the `.dark` class on <html>,
 * which next-themes' inline script sets before first paint.
 *
 * This means MUI colors respond to the theme instantly via CSS — no JS
 * theme swap, no mounted guard, no flash of wrong colors.
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

export const muiTheme = createTheme({
  cssVariables: {
    colorSchemeSelector: ".dark",
  },
  colorSchemes: {
    light: { palette: paletteFrom(tokens.light) },
    dark: { palette: paletteFrom(tokens.dark) },
  },
  defaultColorScheme: "light",
  typography,
  shape,
  components,
});
