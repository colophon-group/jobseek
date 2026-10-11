"use client";

import { Trans } from "@lingui/react/macro";

export function NarrowedMaintenanceNotice() {
  return (
    <div role="status" className="rounded-lg border border-border-soft bg-surface p-4 text-sm">
      <p className="font-semibold">
        <Trans id="narrowed.maintenance.title" comment="Notice heading while Narrowed evaluation is paused for maintenance">
          Narrowed is under maintenance
        </Trans>
      </p>
      <p className="mt-2 text-muted">
        <Trans id="narrowed.maintenance.description" comment="Explains that maintenance preserves saved matching criteria and ordinary search">
          Evaluation is paused. Your saved criteria are kept, and ordinary search and watchlists remain available.
        </Trans>
      </p>
    </div>
  );
}
