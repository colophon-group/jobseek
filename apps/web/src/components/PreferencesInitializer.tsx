"use client";

import { useEffect } from "react";
import { useTheme } from "next-themes";

type Props = {
  theme?: "light" | "dark";
  cookieConsent?: boolean;
};

export function PreferencesInitializer({ theme, cookieConsent }: Props) {
  const { setTheme } = useTheme();

  useEffect(() => {
    if (theme) setTheme(theme);
    if (cookieConsent) localStorage.setItem("cookie-consent", "1");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}
