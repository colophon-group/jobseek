"use client";

import { useState, useEffect } from "react";
import { useTheme } from "next-themes";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Moon, Sun } from "lucide-react";
import { localPrefs } from "@/lib/preference-timestamps";
import { updatePreferences } from "@/lib/actions/preferences";

type ThemeToggleButtonProps = {
  className?: string;
};

export function ThemeToggleButton({ className }: ThemeToggleButtonProps) {
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
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            onClick={() => {
              setTheme(next);
              const now = new Date().toISOString();
              localPrefs.themeTimestamp.set(now);
              void updatePreferences({ theme: next, themeUpdatedAt: now });
            }}
            className={`inline-flex items-center justify-center rounded-md p-1.5 text-foreground hover:bg-border-soft transition-colors cursor-pointer ${className ?? ""}`}
            aria-label={label}
          >
            {isDark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="z-50 rounded-md bg-tooltip-bg px-2.5 py-1 text-xs text-white data-[state=delayed-open]:animate-[tooltip-in_150ms_ease] data-[state=instant-open]:animate-[tooltip-in_150ms_ease] data-[state=closed]:animate-[tooltip-out_100ms_ease_forwards]"
            sideOffset={6}
          >
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
