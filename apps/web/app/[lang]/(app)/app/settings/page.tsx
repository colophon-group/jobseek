import { initI18nForPage } from "@/lib/i18n";
import { GeneralSettings } from "@/components/settings/GeneralSettings";
import { getPreferences, getAvailableJobLanguages } from "@/lib/actions/preferences";
import { getCurrencyRates } from "@/lib/actions/search";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function SettingsPage({ params }: Props) {
  const locale = await initI18nForPage(params);

  const [prefs, availableLanguages, currencyRates] = await Promise.all([
    getPreferences(),
    getAvailableJobLanguages(),
    getCurrencyRates(),
  ]);

  return (
    <GeneralSettings
      savedJobLanguages={prefs?.jobLanguages ?? []}
      savedDisplayCurrency={prefs?.displayCurrency ?? "EUR"}
      availableCurrencies={currencyRates.map((r) => r.currency)}
      availableLanguages={availableLanguages}
      locale={locale}
    />
  );
}
