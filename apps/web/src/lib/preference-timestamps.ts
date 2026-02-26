const THEME_TS_KEY = "pref-theme-updated-at";
const LOCALE_TS_KEY = "pref-locale-updated-at";
const LOCALE_KEY = "pref-locale";

export const localPrefs = {
  themeTimestamp: {
    get: (): string | null => localStorage.getItem(THEME_TS_KEY),
    set: (iso: string) => localStorage.setItem(THEME_TS_KEY, iso),
  },
  localeTimestamp: {
    get: (): string | null => localStorage.getItem(LOCALE_TS_KEY),
    set: (iso: string) => localStorage.setItem(LOCALE_TS_KEY, iso),
  },
  locale: {
    get: (): string | null => localStorage.getItem(LOCALE_KEY),
    set: (locale: string) => localStorage.setItem(LOCALE_KEY, locale),
  },
};
