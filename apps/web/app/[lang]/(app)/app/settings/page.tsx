import { initI18nForPage } from "@/lib/i18n";
import { getPreferences } from "@/lib/actions/preferences";
import { GeneralSettings } from "@/components/settings/GeneralSettings";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function SettingsPage({ params }: Props) {
  await initI18nForPage(params);

  const prefs = await getPreferences();

  return <GeneralSettings initialTheme={prefs?.theme} />;
}
