import { isLocale, defaultLocale } from "@/lib/i18n";
import { BillingLoader } from "./billing-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function BillingSettingsPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <BillingLoader locale={locale} />;
}
