import { getMyJobs } from "@/lib/actions/my-jobs";
import { MyJobsPage } from "./my-jobs-page";

/**
 * The initial page of saved jobs is a read, so resolve it in the server tree
 * instead of issuing an uncached Server Action POST after hydration. The
 * action retains the existing session-enforced data boundary; the parent
 * Suspense boundary streams the same spinner on cold reads.
 */
export async function MyJobsLoader({ locale: _locale }: { locale: string }) {
  const data = await getMyJobs({ offset: 0, limit: 20 });

  return <MyJobsPage initialJobs={data.jobs} initialTotal={data.total} />;
}
