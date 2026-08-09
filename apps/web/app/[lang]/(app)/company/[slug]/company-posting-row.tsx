"use client";

import { Trans, useLingui } from "@lingui/react/macro";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { TrackingDot } from "@/components/TrackingDot";
import { SaveButton } from "@/components/search/save-button";
import type { SearchResultPosting } from "@/lib/search";
import { timeAgoShort } from "@/lib/time";

interface CompanyPostingRowProps {
  posting: SearchResultPosting;
  selected: boolean;
  uiLocale: string;
  onOpen: (postingId: string) => void;
}

export function CompanyPostingRow({
  posting,
  selected,
  uiLocale,
  onOpen,
}: CompanyPostingRowProps) {
  const { t } = useLingui();

  return (
    <div
      data-posting-id={posting.id}
      className={`relative flex items-center gap-2 rounded px-1 py-1.5 transition-colors ${selected ? "bg-primary/10" : "hover:bg-border-soft"} ${posting.isActive === false ? "opacity-50" : ""}`}
    >
      <button
        type="button"
        onClick={() => onOpen(posting.id)}
        aria-label={
          posting.title ??
          t({
            id: "search.card.openPosting",
            comment: "Aria label for opening a job posting from a company card row when the posting title is missing",
            message: "Open job posting",
          })
        }
        className="absolute inset-0 z-0 cursor-pointer rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
      />
      <TrackingDot postingId={posting.id} />
      <span className="min-w-0 flex-1 truncate text-sm">{posting.title ?? "—"}</span>
      {posting.isActive === false && (
        <span className="shrink-0 rounded bg-border-soft px-1 py-0.5 text-[10px] text-muted">
          <Trans id="company.page.closed" comment="Label for inactive/closed job postings on company page">
            Closed
          </Trans>
        </span>
      )}
      {posting.locations.length > 0 && (
        <span className={`shrink-0 text-xs text-muted ${posting.locations[0].geoType && posting.locations[0].geoType !== "city" ? "italic" : ""}`}>
          {posting.locations[0].name}
          {posting.locations.length > 1 && ` +${posting.locations.length - 1}`}
        </span>
      )}
      {!posting.title && (
        <span className="relative z-10 inline-flex shrink-0">
          <PendingJobIcon />
        </span>
      )}
      <span className="relative z-10 shrink-0">
        <SaveButton postingId={posting.id} />
      </span>
      <span suppressHydrationWarning className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted">
        {timeAgoShort(posting.firstSeenAt, uiLocale)}
      </span>
    </div>
  );
}
