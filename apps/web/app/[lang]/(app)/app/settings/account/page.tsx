import { initI18nForPage } from "@/lib/i18n";
import { AccountSettings } from "@/components/settings/AccountSettings";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AccountSettingsPage({ params }: Props) {
  await initI18nForPage(params);

  return <AccountSettings />;
}
