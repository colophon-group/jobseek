import { isLocale, defaultLocale } from "@/lib/i18n";
import { SettingsLoader } from "./settings-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function SettingsPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <SettingsLoader locale={locale} />;
}
