"use client";

import { useTheme } from "next-themes";
import { useLingui } from "@lingui/react/macro";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import type { IconButtonProps } from "@mui/material/IconButton";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";

type ThemeToggleButtonProps = Omit<IconButtonProps, "onClick" | "color">;

export function ThemeToggleButton({ sx, ...iconButtonProps }: ThemeToggleButtonProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const { t } = useLingui();
  const isDark = resolvedTheme === "dark";
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
