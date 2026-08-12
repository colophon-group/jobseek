import { Suspense } from "react";
import { isLocale, defaultLocale } from "@/lib/i18n";
import { BillingLoader } from "./billing-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function BillingSettingsPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return (
    <Suspense fallback={<BillingFallback />}>
      <BillingLoader locale={locale} />
    </Suspense>
  );
}

function BillingFallback() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
    </div>
  );
}
