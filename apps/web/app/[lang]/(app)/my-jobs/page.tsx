import { initI18nForPage } from "@/lib/i18n";
import { getMyJobs } from "@/lib/actions/my-jobs";
import { MyJobsPage } from "./my-jobs-page";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function MyJobsRoute({ params }: Props) {
  await initI18nForPage(params);
  const { jobs, total } = await getMyJobs({ offset: 0, limit: 20 });
  return <MyJobsPage initialJobs={jobs} initialTotal={total} />;
}
