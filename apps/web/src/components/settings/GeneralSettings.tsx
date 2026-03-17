"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useTheme } from "next-themes";
import { useParams, usePathname, useSearchParams, useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { Search } from "lucide-react";
import { locales, type Locale } from "@/lib/i18n";
import { updatePreferences } from "@/lib/actions/preferences";
import type { AvailableLanguage } from "@/lib/actions/preferences";
import { LocaleFlag, localeLabels } from "@/components/flags";
import { CountryFlag } from "@/components/country-flag";
import { localPrefs } from "@/lib/preference-timestamps";
import { getLanguage } from "@/lib/job-languages";
import { JobLanguageModal } from "@/components/settings/JobLanguageModal";

/** How many languages to show inline before the "Find more" button. */
const INLINE_LIMIT = 12;

/* ── Component ── */

interface GeneralSettingsProps {
  savedJobLanguages: string[];
  savedDisplayCurrency: string;
  availableCurrencies: string[];
  availableLanguages: AvailableLanguage[];
  locale: string;
}

export function GeneralSettings({ savedJobLanguages, savedDisplayCurrency, availableCurrencies, availableLanguages, locale: serverLocale }: GeneralSettingsProps) {
  const { theme, setTheme } = useTheme();
  const { t } = useLingui();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const currentLocale = (params.lang as string) ?? serverLocale;
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Job languages state
  // [] = default (show UI locale selected), ["*"] = all languages, ["en","de"] = specific
  const [jobLanguages, setJobLanguages] = useState<string[]>(savedJobLanguages);
  const [langModalOpen, setLangModalOpen] = useState(false);

  // Display currency state
  const [displayCurrency, setDisplayCurrency] = useState(savedDisplayCurrency);

  const isAllLanguages = jobLanguages.includes("*");
  const isDefault = jobLanguages.length === 0;
  // Effective selection: default → current locale, all → nothing, specific → as-is
  const effectiveCodes = isAllLanguages ? [] : isDefault ? [currentLocale] : jobLanguages;
  const selectedLangSet = useMemo(() => new Set(effectiveCodes), [effectiveCodes]);

  // Resolve available language codes (sorted by count desc from server)
  const availableSet = useMemo(
    () => new Set(availableLanguages.map((l) => l.code)),
    [availableLanguages],
  );
  const resolvedLanguages = useMemo(
    () =>
      availableLanguages
        .map((al) => {
          const lang = getLanguage(al.code);
          return lang ? { ...lang, count: al.count } : undefined;
        })
        .filter((l): l is NonNullable<typeof l> => l !== undefined),
    [availableLanguages],
  );
  const inlineLanguages = resolvedLanguages.slice(0, INLINE_LIMIT);
  const hasOverflow = resolvedLanguages.length > INLINE_LIMIT;

  // Also include any selected language not in inline (e.g. picked from modal)
  const extraSelected = useMemo(
    () =>
      effectiveCodes
        .filter(
          (code) =>
            !inlineLanguages.some((l) => l.code === code),
        )
        .map((code) => getLanguage(code))
        .filter((l): l is NonNullable<typeof l> => l !== undefined),
    [effectiveCodes, inlineLanguages],
  );

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

  const handleSelectAllLanguages = useCallback(() => {
    setJobLanguages((prev) => (prev.includes("*") ? [] : ["*"]));
  }, []);

  const handleToggleLanguage = useCallback(
    (code: string) => {
      setJobLanguages((prev) => {
        const wasAll = prev.includes("*");
        const wasDef = prev.length === 0;

        if (wasAll) {
          // Switching from "all" to a specific language
          return [code];
        }

        if (wasDef) {
          // Switching from default (= current locale). If clicking the
          // locale itself, just persist it explicitly; otherwise add both.
          if (code === currentLocale) return [code];
          return [currentLocale, code];
        }

        if (prev.includes(code)) {
          const next = prev.filter((c) => c !== code);
          // If removing the last one, revert to default (UI locale)
          if (next.length === 0) return [];
          return next;
        }

        return [...prev, code];
      });
    },
    [currentLocale],
  );

  // Persist language preference changes (outside updater to avoid setState-during-render)
  const initialLangsRef = useRef(true);
  useEffect(() => {
    if (initialLangsRef.current) {
      initialLangsRef.current = false;
      return;
    }
    void updatePreferences({ jobLanguages });
  }, [jobLanguages]);

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

      {/* Job Languages */}
      <section>
        <h2 className="mb-1 text-lg font-semibold">
          <Trans id="settings.general.jobLanguages.title" comment="Job languages settings section heading">Job languages</Trans>
        </h2>
        <p className="mb-4 text-sm text-muted">
          <Trans id="settings.general.jobLanguages.description" comment="Job languages settings description">
            Choose which languages you want to see job postings in.
          </Trans>
        </p>

        <div className="flex flex-wrap gap-2">
          {/* All languages toggle */}
          <button
            onClick={handleSelectAllLanguages}
            className={`rounded-full border px-4 py-1 text-sm transition-colors cursor-pointer ${
              isAllLanguages
                ? "border-primary bg-primary text-primary-contrast font-semibold"
                : "border-divider bg-surface hover:bg-border-soft"
            }`}
          >
            <Trans id="settings.general.jobLanguages.all" comment="All languages option">All languages</Trans>
          </button>

          {/* Inline languages (sorted by job count from server) */}
          {inlineLanguages.map((lang) => {
            const active = !isAllLanguages && selectedLangSet.has(lang.code);
            return (
              <button
                key={lang.code}
                onClick={() => handleToggleLanguage(lang.code)}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1 text-sm transition-colors ${
                  active
                    ? "bg-primary/10 text-primary font-medium"
                    : isAllLanguages
                      ? "border border-border-soft text-muted opacity-50"
                      : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                }`}
              >
                {lang.flag && <CountryFlag iso={lang.flag} size={16} className="shrink-0 rounded-[2px]" />}
                {lang.label}
              </button>
            );
          })}

          {/* Extra selected languages (picked from modal, not in inline list) */}
          {!isAllLanguages &&
            extraSelected.map((lang) => (
              <button
                key={lang.code}
                onClick={() => handleToggleLanguage(lang.code)}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1 text-sm font-medium text-primary transition-colors"
              >
                {lang.flag && <CountryFlag iso={lang.flag} size={16} className="shrink-0 rounded-[2px]" />}
                {lang.label}
              </button>
            ))}

          {/* Find more button */}
          {hasOverflow && (
            <button
              onClick={() => setLangModalOpen(true)}
              disabled={isAllLanguages}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1 text-sm transition-colors cursor-pointer ${
                isAllLanguages
                  ? "border-border-soft text-muted opacity-50"
                  : "border-dashed border-divider bg-surface hover:bg-border-soft text-muted hover:text-foreground"
              }`}
            >
              <Search size={13} className="shrink-0" />
              <Trans id="settings.general.jobLanguages.findMore" comment="Button to open modal with all languages">
                Find more
              </Trans>
            </button>
          )}
        </div>

        <JobLanguageModal
          open={langModalOpen}
          onOpenChange={setLangModalOpen}
          selected={selectedLangSet}
          onToggle={handleToggleLanguage}
          availableCodes={availableSet}
        />
      </section>

      {/* Display Currency */}
      <section>
        <h2 className="mb-1 text-lg font-semibold">
          <Trans id="settings.general.currency.title" comment="Display currency settings section heading">Display currency</Trans>
        </h2>
        <p className="mb-4 text-sm text-muted">
          <Trans id="settings.general.currency.description" comment="Display currency settings description">
            Choose the currency for salary filters and display.
          </Trans>
        </p>
        <select
          value={displayCurrency}
          onChange={(e) => {
            const val = e.target.value;
            setDisplayCurrency(val);
            void updatePreferences({ displayCurrency: val });
          }}
          className="rounded-md border border-divider bg-surface px-4 py-2 text-sm cursor-pointer"
        >
          {availableCurrencies.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </section>
    </div>
  );
}
