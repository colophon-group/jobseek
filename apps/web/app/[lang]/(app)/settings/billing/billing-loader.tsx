import { getPlanInfo } from "@/lib/actions/billing";
import { BillingSettings } from "@/components/settings/BillingSettings";

/** Resolve the authenticated plan read in the initial server response. */
export async function BillingLoader({ locale: _locale }: { locale: string }) {
  const data = await getPlanInfo();

  return <BillingSettings planInfo={data} />;
}
