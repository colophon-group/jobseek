import { Suspense } from "react";
import { isLocale, defaultLocale } from "@/lib/i18n";
import { AccountLoader } from "./account-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AccountSettingsPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return (
    <Suspense fallback={<AccountFallback />}>
      <AccountLoader locale={locale} />
    </Suspense>
  );
}

function AccountFallback() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
    </div>
  );
}
