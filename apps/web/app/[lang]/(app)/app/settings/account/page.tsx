import { initI18nForPage } from "@/lib/i18n";
import { getAccountPageData } from "@/lib/actions/preferences";
import { AccountSettings } from "@/components/settings/AccountSettings";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AccountSettingsPage({ params }: Props) {
  await initI18nForPage(params);
  const data = await getAccountPageData();

  return <AccountSettings initialData={data} />;
}
