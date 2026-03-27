import { isLocale, defaultLocale } from "@/lib/i18n";
import { ProgressLoader } from "./progress-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function AppPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <ProgressLoader locale={locale} lang={lang} />;
}
