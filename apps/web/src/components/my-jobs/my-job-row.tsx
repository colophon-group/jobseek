"use client";

import Image from "next/image";
import { Building2 } from "lucide-react";
import { timeAgoShort } from "@/lib/time";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import type { MyJobEntry } from "@/lib/actions/my-jobs";

interface MyJobRowProps {
  entry: MyJobEntry;
  isSelected: boolean;
  onSelect: () => void;
}

export function MyJobRow({
  entry,
  isSelected,
  onSelect,
}: MyJobRowProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === "Enter") onSelect(); }}
      className={`flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-border-soft ${
        isSelected ? "bg-border-soft" : ""
      }`}
    >
      {/* Company icon */}
      {entry.company.icon ? (
        <Image
          src={entry.company.icon}
          alt={entry.company.name}
          width={24}
          height={24}
          sizes="24px"
          className="size-6 shrink-0 rounded"
        />
      ) : (
        <div className="flex size-6 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
          <Building2 size={14} />
        </div>
      )}

      {/* Company name */}
      <span className="shrink-0 text-xs text-muted">{entry.company.name}</span>

      {/* Job title */}
      <span className="min-w-0 flex-1 truncate text-sm">
        {entry.posting.title ?? "—"}
      </span>

      {!entry.posting.title && <PendingJobIcon />}

      {/* Time ago */}
      <span
        suppressHydrationWarning
        className="w-8 shrink-0 text-right text-[10px] tabular-nums text-muted"
      >
        {timeAgoShort(entry.statusChangedAt)}
      </span>

      <TrackingDot postingId={entry.posting.id} />
    </div>
  );
}
