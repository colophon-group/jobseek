"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArrowUpDown, ChevronDown, LayoutGrid } from "lucide-react";
import { useLingui } from "@lingui/react";
import { t } from "@lingui/core/macro";

export type SortBy = "status_changed_at" | "saved_at" | "status" | "company_name";
export type GroupBy = "company" | "status";

function useSortOptions(): { value: SortBy; label: string }[] {
  useLingui();
  return [
    { value: "status_changed_at", label: t({ id: "myJobs.sort.recentlyUpdated", comment: "Sort option: recently updated", message: "Recently updated" }) },
    { value: "saved_at", label: t({ id: "myJobs.sort.dateSaved", comment: "Sort option: date saved", message: "Date saved" }) },
    { value: "status", label: t({ id: "myJobs.sort.status", comment: "Sort option: status", message: "Status" }) },
    { value: "company_name", label: t({ id: "myJobs.sort.company", comment: "Sort option: company name", message: "Company" }) },
  ];
}

function useGroupOptions(): { value: GroupBy; label: string }[] {
  useLingui();
  return [
    { value: "company", label: t({ id: "myJobs.group.company", comment: "Group by company option", message: "Company" }) },
    { value: "status", label: t({ id: "myJobs.group.status", comment: "Group by status option", message: "Status" }) },
  ];
}

interface SortFilterBarProps {
  sortBy: SortBy;
  onSortChange: (sortBy: SortBy) => void;
  groupBy: GroupBy;
  onGroupByChange: (groupBy: GroupBy) => void;
}

export function SortFilterBar({
  sortBy,
  onSortChange,
  groupBy,
  onGroupByChange,
}: SortFilterBarProps) {
  const sortOptions = useSortOptions();
  const groupOptions = useGroupOptions();
  const currentSort = sortOptions.find((o) => o.value === sortBy);
  const currentGroup = groupOptions.find((o) => o.value === groupBy);

  return (
    <div className="flex items-center gap-2">
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

      {/* Group by dropdown */}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border-soft px-2.5 py-1.5 text-xs text-muted transition-colors hover:bg-border-soft">
            <LayoutGrid size={12} />
            {currentGroup?.label}
            <ChevronDown size={10} />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            className="z-50 min-w-[140px] rounded-md border border-border-soft bg-surface p-1 shadow-lg"
            sideOffset={5}
          >
            {groupOptions.map((opt) => (
              <DropdownMenu.Item
                key={opt.value}
                className={`flex cursor-pointer items-center rounded-sm px-2 py-1.5 text-xs outline-none hover:bg-border-soft ${
                  groupBy === opt.value ? "font-semibold text-primary" : ""
                }`}
                onSelect={() => onGroupByChange(opt.value)}
              >
                {opt.label}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}
