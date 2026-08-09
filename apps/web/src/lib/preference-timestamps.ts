const THEME_TS_KEY = "pref-theme-updated-at";
const LOCALE_TS_KEY = "pref-locale-updated-at";
const LOCALE_KEY = "pref-locale";
const DISPLAY_CURRENCY_KEY = "pref-display-currency";
const SALARY_PERIOD_KEY = "pref-salary-period";

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
  displayCurrency: {
    get: (): string | null => localStorage.getItem(DISPLAY_CURRENCY_KEY),
    set: (currency: string | null) => {
      if (currency === null) localStorage.removeItem(DISPLAY_CURRENCY_KEY);
      else localStorage.setItem(DISPLAY_CURRENCY_KEY, currency);
    },
  },
  salaryPeriod: {
    get: (): string | null => localStorage.getItem(SALARY_PERIOD_KEY),
    set: (period: string | null) => {
      if (period === null) localStorage.removeItem(SALARY_PERIOD_KEY);
      else localStorage.setItem(SALARY_PERIOD_KEY, period);
    },
  },
};
