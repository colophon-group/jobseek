"use client";

import { useState, useEffect } from "react";
import { useTheme } from "next-themes";
import { useLingui } from "@lingui/react/macro";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import type { IconButtonProps } from "@mui/material/IconButton";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";

type ThemeToggleButtonProps = Omit<IconButtonProps, "onClick" | "color">;

/**
 * Mounted guard: first client render matches the SSG output (dark assumed)
 * to avoid hydration mismatch on icon/aria-label.  After mount, switches
 * to the actual resolved theme.
 *
 * TODO: remove the mounted guard once MUI is phased out — with CSS-only
 * styling the icon can be toggled via .dark class without hydration risk.
 */
export function ThemeToggleButton({ sx, ...iconButtonProps }: ThemeToggleButtonProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const { t } = useLingui();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const isDark = mounted ? resolvedTheme === "dark" : true;
  const next = isDark ? "light" : "dark";

  const label = isDark
    ? t({ id: "common.theme.switchToLight", comment: "Aria label for switching to light mode", message: "Switch to light mode" })
    : t({ id: "common.theme.switchToDark", comment: "Aria label for switching to dark mode", message: "Switch to dark mode" });

  return (
    <Tooltip title={label}>
      <IconButton
        onClick={() => setTheme(next)}
        size="small"
        color="inherit"
        aria-label={label}
        sx={sx}
        {...iconButtonProps}
      >
        {isDark ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}
      </IconButton>
    </Tooltip>
  );
}
