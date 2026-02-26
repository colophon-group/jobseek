"use client";

import { useEffect } from "react";
import { useTheme } from "next-themes";
import { useRouter, usePathname } from "next/navigation";
import { updatePreferences } from "@/lib/actions/preferences";
import { localPrefs } from "@/lib/preference-timestamps";

type Props = {
  theme?: "light" | "dark";
  themeUpdatedAt?: string | null;
  locale?: string;
  localeUpdatedAt?: string | null;
  cookieConsent?: boolean;
};

export function PreferencesInitializer({
  theme: dbTheme,
  themeUpdatedAt: dbThemeTs,
  locale: dbLocale,
  localeUpdatedAt: dbLocaleTs,
  cookieConsent,
}: Props) {
  const { setTheme, resolvedTheme } = useTheme();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (cookieConsent) localStorage.setItem("cookie-consent", "1");

    // ── Theme resolution ──
    const localThemeTs = localPrefs.themeTimestamp.get();

    if (localThemeTs && dbThemeTs && new Date(localThemeTs) > new Date(dbThemeTs)) {
      // Local wins — keep current next-themes value, sync to DB
      if (resolvedTheme && resolvedTheme !== dbTheme) {
        void updatePreferences({
          theme: resolvedTheme as "light" | "dark",
          themeUpdatedAt: localThemeTs,
        });
      }
    } else if (dbTheme) {
      // DB wins — apply server theme
      setTheme(dbTheme);
      if (dbThemeTs) localPrefs.themeTimestamp.set(dbThemeTs);
    }

    // ── Locale resolution ──
    const localLocaleTs = localPrefs.localeTimestamp.get();
    const localLocale = localPrefs.locale.get();
    const urlLocale = pathname.split("/")[1] || "en";

    if (localLocaleTs && dbLocaleTs && new Date(localLocaleTs) > new Date(dbLocaleTs)) {
      // Local wins
      if (localLocale && localLocale !== urlLocale) {
        const newPath = pathname.replace(`/${urlLocale}`, `/${localLocale}`);
        router.push(newPath);
      }
      if (localLocale && localLocale !== dbLocale) {
        void updatePreferences({ locale: localLocale as "en" | "de" | "fr" | "it", localeUpdatedAt: localLocaleTs });
      }
    } else if (dbLocale) {
      // DB wins
      if (dbLocale !== urlLocale) {
        const newPath = pathname.replace(`/${urlLocale}`, `/${dbLocale}`);
        router.push(newPath);
      }
      if (dbLocaleTs) localPrefs.localeTimestamp.set(dbLocaleTs);
      localPrefs.locale.set(dbLocale);
    }
  }, []);

  return null;
}
