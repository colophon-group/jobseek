import { initI18nForPage } from "@/lib/i18n";
import { getSavedJobs } from "@/lib/actions/saved-jobs";
import { SavedPage } from "./saved-page";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function SavedJobsPage({ params }: Props) {
  await initI18nForPage(params);
  const { jobs, total } = await getSavedJobs({ offset: 0, limit: 20 });
  return <SavedPage initialJobs={jobs} initialTotal={total} />;
}
