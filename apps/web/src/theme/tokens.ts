/**
 * Design tokens — single source of truth.
 *
 * Used by:
 *  - createMuiTheme.ts (MUI palette)
 *  - globals.css        (CSS custom properties — must be kept in sync manually)
 *
 * When changing a value here, update the matching variable in globals.css.
 */

export const tokens = {
  light: {
    background: "#f7f8fb",
    surface: "#ffffff",
    surfaceAlpha: "rgba(255, 255, 255, 0.85)",
    foreground: "#18181b",
    muted: "#52525b",
    mutedStrong: "#3f3f46",
    borderSoft: "rgba(24, 24, 27, 0.08)",
    divider: "#d4d4d4",
    primary: "#111111",
    primaryContrast: "#f5f5f5",
    secondary: "#2b2b2b",
    secondaryContrast: "#f5f5f5",
    info: "#1d4ed8",
    success: "#15803d",
    warning: "#b45309",
    error: "#b91c1c",
  },
  dark: {
    background: "#09090b",
    surface: "#0f0f0f",
    surfaceAlpha: "rgba(0, 0, 0, 0.8)",
    foreground: "#f4f4f5",
    muted: "#a1a1aa",
    mutedStrong: "#d4d4d8",
    borderSoft: "rgba(255, 255, 255, 0.08)",
    divider: "#2a2a2a",
    primary: "#f5f5f5",
    primaryContrast: "#09090b",
    secondary: "#dcdcdc",
    secondaryContrast: "#09090b",
    info: "#93bbfd",
    success: "#4ade80",
    warning: "#fb923c",
    error: "#f87171",
  },
} as const;
