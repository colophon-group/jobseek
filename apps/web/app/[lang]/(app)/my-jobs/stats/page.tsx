import { isLocale, defaultLocale } from "@/lib/i18n";
import { StatsLoader } from "./stats-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function MyJobsStatsRoute({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <StatsLoader locale={locale} />;
}
