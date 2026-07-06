"use client";

import { useEffect, useState } from "react";
import { getMyJobsStats, type StatsData } from "@/lib/actions/my-jobs-stats";
import { getViewerTz } from "@/lib/viewer-tz";
import { StatsPage } from "./stats-page";

export function StatsLoader({ locale: _locale }: { locale: string }) {
  const [data, setData] = useState<StatsData | null>(null);

  useEffect(() => {
    // Pass the browser's resolved IANA timezone so server-side day
    // bucketing for the activity heatmap matches what the client
    // renders. See #3199.
    getMyJobsStats({ tz: getViewerTz() }).then(setData);
  }, []);

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return <StatsPage initial={data} />;
}
