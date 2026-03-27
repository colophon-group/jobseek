"use client";

import { useEffect, useState } from "react";
import { getMyJobs } from "@/lib/actions/my-jobs";
import { MyJobsPage } from "./my-jobs-page";

type MyJobsData = Awaited<ReturnType<typeof getMyJobs>>;

export function MyJobsLoader({ locale: _locale }: { locale: string }) {
  const [data, setData] = useState<MyJobsData | null>(null);

  useEffect(() => {
    getMyJobs({ offset: 0, limit: 20 }).then(setData);
  }, []);

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return <MyJobsPage initialJobs={data.jobs} initialTotal={data.total} />;
}
