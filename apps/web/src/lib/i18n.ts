import { type I18n, type Messages, setupI18n } from "@lingui/core";
import { setI18n } from "@lingui/react/server";

export const locales = ["en", "de", "fr", "it"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "en";

export function isLocale(value: string): value is Locale {
  return locales.includes(value as Locale);
}

/**
 * Open Graph locale codes (`language_TERRITORY`) per locale. Used by
 * the OG protocol for `og:locale` and `og:locale:alternate` tags.
 */
const OG_LOCALES: Record<Locale, string> = {
  en: "en_US",
  de: "de_DE",
  fr: "fr_FR",
  it: "it_IT",
};

export function ogLocale(locale: Locale): string {
  return OG_LOCALES[locale];
}

export function ogAlternateLocales(current: Locale): string[] {
  return locales.filter((l) => l !== current).map((l) => OG_LOCALES[l]);
}

type CatalogResult = { i18n: I18n; messages: Messages };
const catalogCache = new Map<string, CatalogResult>();

export async function loadCatalog(locale: Locale): Promise<CatalogResult> {
  if (catalogCache.has(locale)) {
    return catalogCache.get(locale)!;
  }

  const { messages } = await import(`../../locales/${locale}.po`);

  const i18n = setupI18n({
    locale,
    messages: { [locale]: messages },
  });

  const result = { i18n, messages };
  catalogCache.set(locale, result);
  return result;
}

/**
 * Helper for RSC pages: resolves the locale from route params,
 * loads the catalog, and calls setI18n so <Trans> / useLingui work.
 * Returns the resolved locale for any page-level use.
 */
export async function initI18nForPage(params: Promise<{ lang: string }>) {
  const { lang } = await params;
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);
  setI18n(i18n);
  return locale;
}
