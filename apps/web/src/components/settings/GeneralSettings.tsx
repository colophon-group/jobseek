"use client";

import { useState, useEffect, useRef } from "react";
import { useTheme } from "next-themes";
import {
  useParams,
  usePathname,
  useSearchParams,
  useRouter,
} from "next/navigation";
import { Trans, useLingui } from "@lingui/react/macro";
import { Moon, Sun } from "lucide-react";
import { locales, type Locale } from "@/lib/i18n";
import {
  updatePreferences,
  type AvailableLanguage,
} from "@/lib/actions/preferences";
import { LocaleFlag, localeLabels } from "@/components/flags";
import { CountryFlag } from "@/components/country-flag";
import { localPrefs } from "@/lib/preference-timestamps";
import { getLanguage } from "@/lib/job-languages";
import { JobLanguageModal } from "./JobLanguageModal";
import { CurrencyModal } from "./CurrencyModal";
import { SettingsChoice } from "./SettingsChoice";
import { useSalaryDisplay } from "@/components/providers/SalaryDisplayProvider";
import { useSession } from "@/components/providers/SessionProvider";

interface GeneralSettingsProps {
  savedJobLanguages: string[];
  savedDisplayCurrency: string;
  savedSalaryPeriod: string | null;
  availableCurrencies: string[];
  availableLanguages: AvailableLanguage[];
  locale: string;
}

export function GeneralSettings({
  savedJobLanguages,
  savedDisplayCurrency,
  savedSalaryPeriod,
  availableCurrencies,
  availableLanguages,
  locale: serverLocale,
}: GeneralSettingsProps) {
  const { theme, setTheme } = useTheme();
  const { t } = useLingui();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const { isLoggedIn } = useSession();
  const currentLocale = (params.lang as string) ?? serverLocale;
  const [mounted, setMounted] = useState(false);
  const [jobLanguages, setJobLanguages] = useState(savedJobLanguages);
  const [langModalOpen, setLangModalOpen] = useState(false);
  const [displayCurrency, setDisplayCurrency] = useState(savedDisplayCurrency);
  const [salaryPeriod, setSalaryPeriod] = useState(savedSalaryPeriod ?? "");
  const [saveState, setSaveState] = useState<
    "saving" | "saved" | "error" | "locale-error" | null
  >(null);
  const pending = useRef(false);
  const retry = useRef<(() => void) | null>(null);
  const salaryDisplay = useSalaryDisplay();
  useEffect(() => setMounted(true), []);
  useEffect(() => {
    if (salaryDisplay.displayCurrency === null) return;
    setDisplayCurrency(salaryDisplay.displayCurrency);
    setSalaryPeriod(salaryDisplay.displayPeriod ?? "");
  }, [salaryDisplay.displayCurrency, salaryDisplay.displayPeriod]);
  const allLanguages = !jobLanguages.length || jobLanguages.includes("*");
  const busy = saveState === "saving";

  async function save(
    data: Parameters<typeof updatePreferences>[0],
    apply: () => void,
    rollback: () => void,
    confirmed?: () => void,
    failure: "error" | "locale-error" = "error",
  ) {
    if (pending.current) return;
    pending.current = true;
    setSaveState("saving");
    apply();
    try {
      await updatePreferences(data);
      confirmed?.();
      retry.current = null;
      setSaveState("saved");
      router.refresh();
    } catch {
      rollback();
      retry.current = () => {
        void save(data, apply, rollback, confirmed, failure);
      };
      setSaveState(failure);
    } finally {
      pending.current = false;
    }
  }

  function handleLocaleSwitch(locale: Locale) {
    if (locale === currentLocale || pending.current) return;
    const now = new Date().toISOString();
    document.cookie = `NEXT_LOCALE=${locale}; path=/; max-age=31536000; SameSite=Lax`;
    localPrefs.localeTimestamp.set(now);
    localPrefs.locale.set(locale);
    const newPath = pathname.replace(`/${currentLocale}`, `/${locale}`);
    const qs = searchParams.toString();
    router.push(qs ? `${newPath}?${qs}` : newPath);
    // URL/cookie switching is immediate; account sync retains the existing timestamp contract.
    void save(
      { locale, localeUpdatedAt: now },
      () => {},
      () => {},
      undefined,
      "locale-error",
    );
  }

  const row =
    "flex items-start justify-between gap-5 border-b border-border-soft py-5 sm:items-center";
  const muted = "mt-1 text-xs leading-relaxed text-muted";
  return (
    <div>
      <h2 className="mb-7 text-xl font-semibold">
        <Trans
          id="settings.nav.preferences"
          comment="Desktop preferences heading and navigation label"
        >
          Preferences
        </Trans>
      </h2>
      <section aria-labelledby="appearance-heading">
        <h3
          id="appearance-heading"
          className="border-b border-divider pb-3 text-sm font-semibold"
        >
          <Trans
            id="settings.general.appearance"
            comment="Group containing theme and app language"
          >
            Appearance
          </Trans>
        </h3>
        <div className={row}>
          <span className="text-sm">
            <Trans
              id="settings.general.theme.title"
              comment="Theme settings section heading"
            >
              Theme
            </Trans>
          </span>
          <div className="inline-flex shrink-0 gap-1 rounded-lg bg-border-soft p-1">
            {(
              [
                [
                  "light",
                  Sun,
                  t({
                    id: "settings.theme.light",
                    comment: "Light theme option",
                    message: "Light",
                  }),
                ],
                [
                  "dark",
                  Moon,
                  t({
                    id: "settings.theme.dark",
                    comment: "Dark theme option",
                    message: "Dark",
                  }),
                ],
              ] as const
            ).map(([value, Icon, label]) => (
              <button
                key={value}
                disabled={busy}
                aria-pressed={mounted && theme === value}
                onClick={() => {
                  const previous = theme;
                  const now = new Date().toISOString();
                  void save(
                    { theme: value, themeUpdatedAt: now },
                    () => setTheme(value),
                    () => setTheme(previous ?? "light"),
                    () => localPrefs.themeTimestamp.set(now),
                  );
                }}
                className={`inline-flex min-h-10 items-center gap-2 rounded-md px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 ${mounted && theme === value ? "bg-surface shadow-sm" : "text-muted"}`}
              >
                <Icon size={14} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className={`${row} flex-col sm:flex-row`}>
          <div>
            <span className="text-sm">
              <Trans
                id="settings.general.appLanguage"
                comment="Language of app menus and buttons, separate from job languages"
              >
                App language
              </Trans>
            </span>
            <p className={muted}>
              <Trans
                id="settings.general.appLanguageHelp"
                comment="Brief app-language description"
              >
                Menus and buttons.
              </Trans>
            </p>
          </div>
          <div className="grid w-full grid-cols-2 gap-1 sm:w-72">
            {locales.map((locale) => (
              <button
                key={locale}
                aria-pressed={locale === currentLocale}
                disabled={busy}
                onClick={() => handleLocaleSwitch(locale)}
                className={`flex min-h-11 items-center gap-2 rounded-lg border px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 ${locale === currentLocale ? "border-divider bg-surface font-semibold" : "border-transparent text-muted hover:bg-border-soft"}`}
              >
                <LocaleFlag locale={locale} size={18} />
                {localeLabels[locale]}
              </button>
            ))}
          </div>
        </div>
      </section>
      <section className="mt-7" aria-labelledby="job-results-heading">
        <h3
          id="job-results-heading"
          className="border-b border-divider pb-3 text-sm font-semibold"
        >
          <Trans
            id="settings.general.results"
            comment="Group containing job-language and salary-display preferences"
          >
            Job results
          </Trans>
        </h3>
        <div className={row}>
          <div className="min-w-0">
            <span className="text-sm">
              <Trans
                id="settings.jobLanguages.title"
                comment="Job posting language preferences heading"
              >
                Job languages
              </Trans>
            </span>
            <p className={muted}>
              <Trans
                id="settings.general.jobLanguageHelp"
                comment="Brief explanation of the job-language filter"
              >
                Which postings appear in your results.
              </Trans>
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {allLanguages ? (
                <span className="rounded-full bg-border-soft px-2.5 py-1 text-[11px]">
                  <Trans
                    id="settings.jobLanguages.all"
                    comment="All job languages option"
                  >
                    All languages
                  </Trans>
                </span>
              ) : (
                jobLanguages.map((code) => {
                  const lang = getLanguage(code);
                  return (
                    <span
                      key={code}
                      className="inline-flex items-center gap-1.5 rounded-full bg-border-soft px-2.5 py-1 text-[11px]"
                    >
                      {lang?.flag && <CountryFlag iso={lang.flag} size={14} />}
                      {lang?.label ?? code}
                    </span>
                  );
                })
              )}
            </div>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => setLangModalOpen(true)}
            className="min-h-11 shrink-0 rounded-full border border-divider px-4 text-xs font-semibold hover:bg-border-soft disabled:opacity-50"
          >
            <Trans
              id="settings.general.choose"
              comment="Open a preference picker"
            >
              Choose
            </Trans>
          </button>
        </div>
        <div className={`${row} flex-col sm:flex-row`}>
          <div>
            <span className="text-sm">
              <Trans
                id="settings.general.salary.title"
                comment="Salary display settings section heading"
              >
                Salary display
              </Trans>
            </span>
            <p className={muted}>
              <Trans
                id="settings.general.salaryHelp"
                comment="Brief salary-display description"
              >
                Currency and pay period.
              </Trans>
            </p>
          </div>
          <div className="flex w-full gap-3 sm:w-auto">
            <div className="min-w-0 flex-1 sm:w-24">
              <label className="mb-2 block text-[11px] text-muted">
                <Trans
                  id="settings.general.salary.currencyLabel"
                  comment="Label for currency selector"
                >
                  Currency
                </Trans>
              </label>
              <CurrencyModal
                value={displayCurrency}
                currencies={availableCurrencies}
                disabled={busy}
                onSelect={(code) => {
                  const previous = displayCurrency;
                  void save(
                    { displayCurrency: code },
                    () => setDisplayCurrency(code),
                    () => setDisplayCurrency(previous),
                    () => salaryDisplay.update({ displayCurrency: code }),
                  );
                }}
              />
            </div>
            <div className="min-w-0 flex-1 sm:w-36">
              <span className="mb-2 block text-[11px] text-muted">
                <Trans
                  id="settings.general.salary.periodLabel"
                  comment="Label for pay period selector"
                >
                  Pay period
                </Trans>
              </span>
              <SettingsChoice
                label={t({
                  id: "settings.general.salary.periodLabel",
                  comment: "Label for pay period selector",
                  message: "Pay period",
                })}
                value={salaryPeriod}
                disabled={busy}
                options={[
                  {
                    value: "",
                    label: t({
                      id: "settings.general.salary.original",
                      comment: "Keep the job posting's original pay period",
                      message: "Original",
                    }),
                  },
                  {
                    value: "yearly",
                    label: t({
                      id: "settings.general.salary.yearly",
                      comment: "Yearly salary period",
                      message: "Yearly",
                    }),
                  },
                  {
                    value: "monthly",
                    label: t({
                      id: "settings.general.salary.monthly",
                      comment: "Monthly salary period",
                      message: "Monthly",
                    }),
                  },
                  {
                    value: "daily",
                    label: t({
                      id: "settings.general.salary.daily",
                      comment: "Daily salary period",
                      message: "Daily",
                    }),
                  },
                  {
                    value: "hourly",
                    label: t({
                      id: "settings.general.salary.hourly",
                      comment: "Hourly salary period",
                      message: "Hourly",
                    }),
                  },
                ]}
                onChange={(value) => {
                  const previous = salaryPeriod;
                  void save(
                    { salaryPeriod: value || null },
                    () => setSalaryPeriod(value),
                    () => setSalaryPeriod(previous),
                    () => salaryDisplay.update({ salaryPeriod: value || null }),
                  );
                }}
              />
            </div>
          </div>
        </div>
      </section>
      <p
        role="status"
        aria-live="polite"
        className="mt-4 min-h-5 text-xs text-muted"
      >
        {saveState === "saving" && (
          <Trans
            id="settings.preferences.saving"
            comment="Preference persistence status"
          >
            Saving…
          </Trans>
        )}
        {saveState === "saved" && (
          <Trans
            id="settings.preferences.saved"
            comment="Preference persistence status"
          >
            Saved
          </Trans>
        )}
        {(saveState === "error" || saveState === "locale-error") && (
          <>
            {saveState === "locale-error" ? (
              <Trans
                id="settings.preferences.localeError"
                comment="The app language changed locally but account synchronization failed"
              >
                Language changed on this device. Could not sync your account.
              </Trans>
            ) : (
              <Trans
                id="settings.preferences.error"
                comment="Preference persistence failed and optimistic value was restored"
              >
                Could not save. Your previous choice is still active.
              </Trans>
            )}
            {retry.current && (
              <button
                onClick={() => retry.current?.()}
                className="ml-2 underline underline-offset-4"
              >
                <Trans
                  id="common.actions.retry"
                  comment="Retry a failed preference save"
                >
                  Retry
                </Trans>
              </button>
            )}
          </>
        )}
      </p>
      {!isLoggedIn && (
        <p className="mt-2 text-xs text-muted">
          <Trans
            id="settings.preferences.local"
            comment="Anonymous preference persistence notice"
          >
            Saved on this device. Sign in to sync.
          </Trans>
        </p>
      )}
      <JobLanguageModal
        open={langModalOpen}
        onOpenChange={setLangModalOpen}
        selected={new Set(allLanguages ? ["*"] : jobLanguages)}
        availableCodes={
          new Set(availableLanguages.map((language) => language.code))
        }
        locale={currentLocale}
        onApply={(next) => {
          const previous = jobLanguages;
          void save(
            { jobLanguages: next },
            () => setJobLanguages(next),
            () => setJobLanguages(previous),
          );
        }}
      />
    </div>
  );
}
