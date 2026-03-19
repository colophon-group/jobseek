"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArrowUpDown, ChevronDown, LayoutGrid } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { tooltipClass } from "@/components/ui/tooltip-styles";
import { Trans, useLingui } from "@lingui/react/macro";
import {
  APPLICATION_STATUSES,
  type ApplicationStatus,
} from "@/lib/actions/my-jobs";

type SortBy = "status_changed_at" | "saved_at" | "status" | "company_name";

const sortOptions: { value: SortBy; label: string }[] = [
  { value: "status_changed_at", label: "Recently updated" },
  { value: "saved_at", label: "Date saved" },
  { value: "status", label: "Status" },
  { value: "company_name", label: "Company" },
];

const statusLabels: Record<ApplicationStatus, string> = {
  saved: "Saved",
  applied: "Applied",
  interviewing: "Interviewing",
  offered: "Offered",
  rejected: "Rejected",
};

interface SortFilterBarProps {
  sortBy: SortBy;
  onSortChange: (sortBy: SortBy) => void;
  statusFilter: ApplicationStatus[];
  onStatusFilterChange: (filter: ApplicationStatus[]) => void;
  groupByCompany: boolean;
  onGroupByCompanyChange: (enabled: boolean) => void;
}

export function SortFilterBar({
  sortBy,
  onSortChange,
  statusFilter,
  onStatusFilterChange,
  groupByCompany,
  onGroupByCompanyChange,
}: SortFilterBarProps) {
  const { t } = useLingui();
  const currentSort = sortOptions.find((o) => o.value === sortBy);

  function toggleStatus(status: ApplicationStatus) {
    if (statusFilter.includes(status)) {
      onStatusFilterChange(statusFilter.filter((s) => s !== status));
    } else {
      onStatusFilterChange([...statusFilter, status]);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Sort dropdown */}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border-soft px-2.5 py-1.5 text-xs text-muted transition-colors hover:bg-border-soft">
            <ArrowUpDown size={12} />
            {currentSort?.label}
            <ChevronDown size={10} />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            className="z-50 min-w-[160px] rounded-md border border-border-soft bg-surface p-1 shadow-lg"
            sideOffset={5}
          >
            {sortOptions.map((opt) => (
              <DropdownMenu.Item
                key={opt.value}
                className={`flex cursor-pointer items-center rounded-sm px-2 py-1.5 text-xs outline-none hover:bg-border-soft ${
                  sortBy === opt.value ? "font-semibold text-primary" : ""
                }`}
                onSelect={() => onSortChange(opt.value)}
              >
                {opt.label}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      {/* Status filter pills */}
      <div className="flex flex-wrap items-center gap-1">
        <button
          onClick={() => onStatusFilterChange([])}
          className={`cursor-pointer rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
            statusFilter.length === 0
              ? "bg-primary text-primary-contrast"
              : "bg-border-soft text-muted hover:text-foreground"
          }`}
        >
          <Trans
            id="myJobs.filter.all"
            comment="Filter pill to show all statuses"
          >
            All
          </Trans>
        </button>
        {APPLICATION_STATUSES.map((status) => (
          <button
            key={status}
            onClick={() => toggleStatus(status)}
            className={`cursor-pointer rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
              statusFilter.includes(status)
                ? "bg-primary text-primary-contrast"
                : "bg-border-soft text-muted hover:text-foreground"
            }`}
          >
            {statusLabels[status]}
          </button>
        ))}
      </div>

      {/* Group by company toggle */}
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            onClick={() => onGroupByCompanyChange(!groupByCompany)}
            className={`ml-auto inline-flex cursor-pointer items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs transition-colors ${
              groupByCompany
                ? "border-primary bg-primary/10 text-primary"
                : "border-border-soft text-muted hover:bg-border-soft"
            }`}
          >
            <LayoutGrid size={12} />
            <span className="hidden sm:inline">
              <Trans
                id="myJobs.groupByCompany"
                comment="Toggle to group jobs by company"
              >
                Group by company
              </Trans>
            </span>
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className={tooltipClass} sideOffset={6}>
            {t({
              id: "myJobs.groupByCompany.tooltip",
              comment: "Tooltip for group by company toggle",
              message: "Group by company",
            })}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </div>
  );
}
