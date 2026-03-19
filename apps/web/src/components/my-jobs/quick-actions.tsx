"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import { Check, X, Plus, Star } from "lucide-react";
import { tooltipClass } from "@/components/ui/tooltip-styles";
import type { ApplicationStatus } from "@/lib/actions/my-jobs";

type Action = {
  label: string;
  icon: React.ReactNode;
  status: ApplicationStatus;
  className: string;
};

const actionsByStatus: Record<ApplicationStatus, Action[]> = {
  saved: [
    {
      label: "Mark applied",
      icon: <Check size={12} />,
      status: "applied",
      className: "text-blue-600 hover:bg-blue-100 dark:hover:bg-blue-900/40",
    },
    {
      label: "Mark rejected",
      icon: <X size={12} />,
      status: "rejected",
      className: "text-red-500 hover:bg-red-100 dark:hover:bg-red-900/40",
    },
  ],
  applied: [
    {
      label: "Add interview",
      icon: <Plus size={12} />,
      status: "interviewing",
      className:
        "text-amber-600 hover:bg-amber-100 dark:hover:bg-amber-900/40",
    },
    {
      label: "Mark rejected",
      icon: <X size={12} />,
      status: "rejected",
      className: "text-red-500 hover:bg-red-100 dark:hover:bg-red-900/40",
    },
  ],
  interviewing: [
    {
      label: "Add interview",
      icon: <Plus size={12} />,
      status: "interviewing",
      className:
        "text-amber-600 hover:bg-amber-100 dark:hover:bg-amber-900/40",
    },
    {
      label: "Mark offer",
      icon: <Star size={12} />,
      status: "offered",
      className:
        "text-green-600 hover:bg-green-100 dark:hover:bg-green-900/40",
    },
    {
      label: "Mark rejected",
      icon: <X size={12} />,
      status: "rejected",
      className: "text-red-500 hover:bg-red-100 dark:hover:bg-red-900/40",
    },
  ],
  offered: [
    {
      label: "Mark rejected",
      icon: <X size={12} />,
      status: "rejected",
      className: "text-red-500 hover:bg-red-100 dark:hover:bg-red-900/40",
    },
  ],
  rejected: [],
};

interface QuickActionsProps {
  status: ApplicationStatus;
  onStatusChange: (newStatus: ApplicationStatus) => void;
  onAddInterview?: () => void;
}

export function QuickActions({
  status,
  onStatusChange,
  onAddInterview,
}: QuickActionsProps) {
  const actions = actionsByStatus[status];
  if (actions.length === 0) return null;

  return (
    <span className="inline-flex items-center gap-0.5">
      {actions.map((action) => (
        <Tooltip.Root key={action.label}>
          <Tooltip.Trigger asChild>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (
                  action.status === "interviewing" &&
                  status !== "saved" &&
                  onAddInterview
                ) {
                  onAddInterview();
                } else {
                  onStatusChange(action.status);
                }
              }}
              className={`inline-flex cursor-pointer items-center justify-center rounded p-1 transition-colors ${action.className}`}
            >
              {action.icon}
            </button>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content className={tooltipClass} sideOffset={6}>
              {action.label}
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      ))}
    </span>
  );
}
