import { getAccountPageData } from "@/lib/actions/preferences";
import { AccountSettings } from "@/components/settings/AccountSettings";

/** Resolve the session-scoped account read in the initial server response. */
export async function AccountLoader({ locale: _locale }: { locale: string }) {
  const data = await getAccountPageData();

  return <AccountSettings initialData={data} />;
}
