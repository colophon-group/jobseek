import { isLocale, defaultLocale } from "@/lib/i18n";
import { AccountLoader } from "./account-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AccountSettingsPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <AccountLoader locale={locale} />;
}
