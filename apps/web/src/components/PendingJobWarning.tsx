"use client";

import { AlertTriangle } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { tooltipClass } from "@/components/ui/tooltip-styles";
import { Trans } from "@lingui/react/macro";

/**
 * Small warning icon for job lists — shown when a posting has no title.
 * Includes its own Tooltip.Provider so it can be used anywhere.
 */
export function PendingJobIcon() {
  return (
    <Tooltip.Provider delayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span className="inline-flex shrink-0 text-amber-500">
            <AlertTriangle size={13} />
          </span>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className={tooltipClass} sideOffset={6}>
            <Trans id="job.pending.tooltip" comment="Tooltip for pending job warning icon">
              We noticed this job was recently added. Details are being processed.
            </Trans>
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

/**
 * Full warning banner for the job detail panel — shown when title or description is missing.
 */
export function PendingJobBanner() {
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <p>
        <Trans id="job.pending.banner" comment="Warning banner shown when job details are still being processed">
          We noticed this job was recently added. Some details may still be processing and will appear shortly.
        </Trans>
      </p>
    </div>
  );
}
