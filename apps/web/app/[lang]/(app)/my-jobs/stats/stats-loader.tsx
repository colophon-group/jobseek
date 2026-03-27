"use client";

import { useEffect, useState } from "react";
import { getStats, type StatsData } from "@/lib/actions/my-jobs-stats";
import { StatsPage } from "./stats-page";

export function StatsLoader({ locale: _locale }: { locale: string }) {
  const [data, setData] = useState<StatsData | null>(null);

  useEffect(() => {
    getStats().then(setData);
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
