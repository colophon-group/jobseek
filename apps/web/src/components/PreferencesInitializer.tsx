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
      // Local wins — sync cookie so proxy redirects correctly
      if (localLocale) {
        document.cookie = `NEXT_LOCALE=${localLocale}; path=/; max-age=31536000; SameSite=Lax`;
      }
      if (localLocale && localLocale !== urlLocale) {
        const newPath = pathname.replace(`/${urlLocale}`, `/${localLocale}`);
        router.replace(newPath);
      }
      if (localLocale && localLocale !== dbLocale) {
        void updatePreferences({ locale: localLocale as "en" | "de" | "fr" | "it", localeUpdatedAt: localLocaleTs });
      }
    } else if (dbLocale) {
      // DB wins — sync cookie so proxy redirects correctly on next request
      document.cookie = `NEXT_LOCALE=${dbLocale}; path=/; max-age=31536000; SameSite=Lax`;
      if (dbLocale !== urlLocale) {
        const newPath = pathname.replace(`/${urlLocale}`, `/${dbLocale}`);
        router.replace(newPath);
      }
      if (dbLocaleTs) localPrefs.localeTimestamp.set(dbLocaleTs);
      localPrefs.locale.set(dbLocale);
    }
  }, []);

  return null;
}
