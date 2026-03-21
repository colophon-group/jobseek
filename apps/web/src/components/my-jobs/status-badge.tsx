"use client";

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

const statusLabels: Record<ApplicationStatus, string> = {
  saved: "Saved",
  applied: "Applied",
  interviewing: "Interviewing",
  offered: "Offer",
  rejected: "Rejected",
};

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${statusStyles[status]}`}
    >
      {statusLabels[status]}
    </span>
  );
}
