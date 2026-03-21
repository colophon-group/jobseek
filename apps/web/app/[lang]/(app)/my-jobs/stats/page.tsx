import { initI18nForPage } from "@/lib/i18n";
import { getStats } from "@/lib/actions/my-jobs-stats";
import { StatsPage } from "./stats-page";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function MyJobsStatsRoute({ params }: Props) {
  await initI18nForPage(params);
  const initial = await getStats();
  return <StatsPage initial={initial} />;
}
