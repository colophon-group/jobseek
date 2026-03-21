"use client";

import { useSavedJobs } from "@/components/SavedJobsProvider";

const statusColor: Record<string, string> = {
  saved: "bg-muted",
  applied: "bg-sky-400 dark:bg-sky-500",
  interviewing: "bg-amber-400 dark:bg-amber-500",
  offered: "bg-emerald-400 dark:bg-emerald-500",
  rejected: "bg-rose-400 dark:bg-rose-500",
};

export function TrackingDot({ postingId }: { postingId: string }) {
  const { getStatus } = useSavedJobs();
  const status = getStatus(postingId);

  if (!status) {
    return <span className="inline-block size-2 shrink-0 rounded-full border border-muted" />;
  }

  return <span className={`inline-block size-2 shrink-0 rounded-full ${statusColor[status] ?? "bg-muted"}`} />;
}
