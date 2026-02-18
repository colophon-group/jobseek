import { initI18nForPage } from "@/lib/i18n";
import { LicenseContent } from "@/components/LicenseContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function LicensePage({ params }: Props) {
  await initI18nForPage(params);
  return <LicenseContent />;
}
