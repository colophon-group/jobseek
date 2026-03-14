"use client";

import { useState, useEffect } from "react";
import { useTheme } from "next-themes";
import { useParams, usePathname, useSearchParams, useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { locales, type Locale } from "@/lib/i18n";
import { updatePreferences } from "@/lib/actions/preferences";
import { LocaleFlag, localeLabels } from "@/components/flags";
import { localPrefs } from "@/lib/preference-timestamps";

/* ── Component ── */

export function GeneralSettings() {
  const { theme, setTheme } = useTheme();
  const { t } = useLingui();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const currentLocale = (params.lang as string) ?? "en";
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const themeOptions = [
    { value: "light", label: t({ id: "settings.theme.light", comment: "Light theme option", message: "Light" }) },
    { value: "dark", label: t({ id: "settings.theme.dark", comment: "Dark theme option", message: "Dark" }) },
  ];

  function handleLocaleSwitch(locale: Locale) {
    if (locale === currentLocale) return;
    const now = new Date().toISOString();
    localPrefs.localeTimestamp.set(now);
    localPrefs.locale.set(locale);
    const newPath = pathname.replace(`/${currentLocale}`, `/${locale}`);
    const qs = searchParams.toString();
    router.push(qs ? `${newPath}?${qs}` : newPath);
    void updatePreferences({ locale, localeUpdatedAt: now });
  }

  return (
    <div className="space-y-10">
      {/* Theme */}
      <section>
        <h2 className="mb-1 text-lg font-semibold">
          <Trans id="settings.general.theme.title" comment="Theme settings section heading">Theme</Trans>
        </h2>
        <p className="mb-4 text-sm text-muted">
          <Trans id="settings.general.theme.description" comment="Theme settings description">Choose how Job Seek looks to you.</Trans>
        </p>
        <div className="flex gap-2">
          {themeOptions.map((opt) => (
            <button
              key={opt.value}
              onClick={() => {
                const now = new Date().toISOString();
                setTheme(opt.value);
                localPrefs.themeTimestamp.set(now);
                void updatePreferences({ theme: opt.value as "light" | "dark", themeUpdatedAt: now });
              }}
              className={`rounded-md border px-4 py-2 text-sm transition-colors cursor-pointer ${
                mounted && theme === opt.value
                  ? "border-primary bg-primary text-primary-contrast font-semibold"
                  : "border-divider bg-surface hover:bg-border-soft"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </section>

      {/* Language */}
      <section>
        <h2 className="mb-1 text-lg font-semibold">
          <Trans id="settings.general.language.title" comment="Language settings section heading">Language</Trans>
        </h2>
        <p className="mb-4 text-sm text-muted">
          <Trans id="settings.general.language.description" comment="Language settings description">Select your preferred language.</Trans>
        </p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {locales.map((locale) => {
            const isActive = locale === currentLocale;
            return (
              <button
                key={locale}
                onClick={() => handleLocaleSwitch(locale)}
                className={`flex items-center gap-2 rounded-md border px-4 py-2.5 text-sm transition-colors cursor-pointer ${
                  isActive
                    ? "border-primary bg-primary text-primary-contrast font-semibold"
                    : "border-divider bg-surface hover:bg-border-soft"
                }`}
              >
                <LocaleFlag locale={locale} size={20} className="shrink-0" />
                <span>{localeLabels[locale]}</span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
