import { initI18nForPage } from "@/lib/i18n";
import { MarketingPage, marketingMetadata, type MarketingPageProps } from "@/components/marketing/MarketingPage";

export async function generateMetadata({ params }: MarketingPageProps) {
  return marketingMetadata("tracker", params);
}

export default async function Page({ params }: MarketingPageProps) {
  const locale = await initI18nForPage(params);
  return <MarketingPage kind="tracker" locale={locale} />;
}
