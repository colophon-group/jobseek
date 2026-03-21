"use client";

import { useLingui } from "@lingui/react";
import { t } from "@lingui/core/macro";
import type { ApplicationStatus } from "@/lib/actions/my-jobs";

const statusStyles: Record<ApplicationStatus, string> = {
  saved: "bg-border-soft text-muted",
  applied: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  interviewing:
    "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  offered:
    "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  rejected: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

function useStatusLabels(): Record<ApplicationStatus, string> {
  useLingui();
  return {
    saved: t({ id: "myJobs.status.saved", comment: "Saved status badge label", message: "Saved" }),
    applied: t({ id: "myJobs.status.applied", comment: "Applied status badge label", message: "Applied" }),
    interviewing: t({ id: "myJobs.status.interviewing", comment: "Interviewing status badge label", message: "Interviewing" }),
    offered: t({ id: "myJobs.status.offered", comment: "Offered status badge label", message: "Offer" }),
    rejected: t({ id: "myJobs.status.rejected", comment: "Rejected status badge label", message: "Rejected" }),
  };
}

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  const statusLabels = useStatusLabels();
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${statusStyles[status]}`}
    >
      {statusLabels[status]}
    </span>
  );
}
