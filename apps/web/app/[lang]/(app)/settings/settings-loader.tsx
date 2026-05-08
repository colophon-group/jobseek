"use client";

import { useEffect, useState } from "react";
import { GeneralSettings } from "@/components/settings/GeneralSettings";
import {
  getPreferences,
  getAvailableJobLanguages,
  getViewerJobLanguages,
  type AvailableLanguage,
} from "@/lib/actions/preferences";
import { getCurrencyRates } from "@/lib/actions/search";

type SettingsData = {
  jobLanguages: string[];
  displayCurrency: string;
  salaryPeriod: string | null;
  availableCurrencies: string[];
  availableLanguages: AvailableLanguage[];
};

export function SettingsLoader({ locale }: { locale: string }) {
  const [data, setData] = useState<SettingsData | null>(null);

  useEffect(() => {
    // ``getViewerJobLanguages`` unifies the auth (DB row) and anon
    // (cookie) paths so the toggle reflects the persisted state for
    // both. Other prefs (currency / salary period) are auth-only —
    // the anon defaults are baked into ``GeneralSettings``.
    Promise.all([
      getPreferences(),
      getViewerJobLanguages(),
      getAvailableJobLanguages(),
      getCurrencyRates(),
    ]).then(([prefs, jobLanguages, availableLanguages, currencyRates]) => {
      setData({
        jobLanguages,
        displayCurrency: prefs?.displayCurrency ?? "EUR",
        salaryPeriod: prefs?.salaryPeriod ?? null,
        availableCurrencies: currencyRates.map((r) => r.currency),
        availableLanguages,
      });
    });
  }, []);

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return (
    <GeneralSettings
      savedJobLanguages={data.jobLanguages}
      savedDisplayCurrency={data.displayCurrency}
      savedSalaryPeriod={data.salaryPeriod}
      availableCurrencies={data.availableCurrencies}
      availableLanguages={data.availableLanguages}
      locale={locale}
    />
  );
}
