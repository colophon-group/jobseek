"use client";

import { useState, useEffect, useTransition } from "react";
import { useTheme } from "next-themes";
import { useParams, usePathname, useSearchParams, useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { locales, type Locale } from "@/lib/i18n";
import { updatePreferences } from "@/lib/actions/preferences";
import type { SVGProps } from "react";

/* ── Flags (reused from LocaleSwitcher) ── */

function FlagGB(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#012169" d="M0 0h640v480H0z"/>
      <path fill="#FFF" d="m75 0 244 181L562 0h78v62L400 241l240 178v61h-80L320 301 81 480H0v-60l239-178L0 64V0z"/>
      <path fill="#C8102E" d="m424 281 216 159v40L369 281zm-184 20 6 35L54 480H0zM640 0v3L391 191l2-44L590 0zM0 0l239 176h-60L0 42z"/>
      <path fill="#FFF" d="M241 0v480h160V0zM0 160v160h640V160z"/>
      <path fill="#C8102E" d="M0 193v96h640v-96zM273 0v480h96V0z"/>
    </svg>
  );
}

function FlagDE(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#fc0" d="M0 320h640v160H0z"/>
      <path fill="#000001" d="M0 0h640v160H0z"/>
      <path fill="red" d="M0 160h640v160H0z"/>
    </svg>
  );
}

function FlagFR(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <path fill="#fff" d="M0 0h640v480H0z"/>
      <path fill="#000091" d="M0 0h213.3v480H0z"/>
      <path fill="#e1000f" d="M426.7 0H640v480H426.7z"/>
    </svg>
  );
}

function FlagIT(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480" {...props}>
      <g fillRule="evenodd" strokeWidth="1pt">
        <path fill="#fff" d="M0 0h640v480H0z"/>
        <path fill="#009246" d="M0 0h213.3v480H0z"/>
        <path fill="#ce2b37" d="M426.7 0H640v480H426.7z"/>
      </g>
    </svg>
  );
}

const flags: Record<Locale, typeof FlagGB> = { en: FlagGB, de: FlagDE, fr: FlagFR, it: FlagIT };

const localeLabels: Record<Locale, string> = {
  en: "English",
  de: "Deutsch",
  fr: "Français",
  it: "Italiano",
};

/* ── Component ── */

export function GeneralSettings({
  initialTheme,
}: {
  initialTheme?: string | null;
}) {
  const { theme, setTheme } = useTheme();
  const { t } = useLingui();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const currentLocale = (params.lang as string) ?? "en";
  const [mounted, setMounted] = useState(false);
  const [, startTransition] = useTransition();
  useEffect(() => setMounted(true), []);

  // Sync theme with server-provided preference (no fetch needed)
  useEffect(() => {
    if (initialTheme && initialTheme !== theme) setTheme(initialTheme);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const themeOptions = [
    { value: "light", label: t({ id: "settings.theme.light", comment: "Light theme option", message: "Light" }) },
    { value: "dark", label: t({ id: "settings.theme.dark", comment: "Dark theme option", message: "Dark" }) },
  ];

  function handleLocaleSwitch(locale: Locale) {
    if (locale === currentLocale) return;
    const newPath = pathname.replace(`/${currentLocale}`, `/${locale}`);
    const qs = searchParams.toString();
    router.push(qs ? `${newPath}?${qs}` : newPath);
    startTransition(() => {
      updatePreferences({ locale });
    });
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
                setTheme(opt.value);
                startTransition(() => {
                  updatePreferences({ theme: opt.value as "light" | "dark" });
                });
              }}
              className={`rounded-md border px-4 py-2 text-sm transition-colors cursor-pointer ${
                mounted && theme === opt.value
                  ? "border-primary bg-primary text-primary-contrast font-semibold"
                  : "border-border-soft bg-surface hover:bg-border-soft"
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
            const Flag = flags[locale];
            const isActive = locale === currentLocale;
            return (
              <button
                key={locale}
                onClick={() => handleLocaleSwitch(locale)}
                className={`flex items-center gap-2 rounded-md border px-4 py-2.5 text-sm transition-colors cursor-pointer ${
                  isActive
                    ? "border-primary bg-primary text-primary-contrast font-semibold"
                    : "border-border-soft bg-surface hover:bg-border-soft"
                }`}
              >
                <Flag width={20} height={15} className="shrink-0" aria-hidden />
                <span>{localeLabels[locale]}</span>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
