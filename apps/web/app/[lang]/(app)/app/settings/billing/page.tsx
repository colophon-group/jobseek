import { initI18nForPage } from "@/lib/i18n";
import { getPlanInfo } from "@/lib/actions/billing";
import { BillingSettings } from "@/components/settings/BillingSettings";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function BillingSettingsPage({ params }: Props) {
  await initI18nForPage(params);
  const planInfo = await getPlanInfo();

  return <BillingSettings planInfo={planInfo} />;
}
