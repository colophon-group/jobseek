import { initI18nForPage } from "@/lib/i18n";
import { HowWeIndexContent } from "@/components/HowWeIndexContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function HowWeIndexPage({ params }: Props) {
  await initI18nForPage(params);
  return <HowWeIndexContent />;
}
