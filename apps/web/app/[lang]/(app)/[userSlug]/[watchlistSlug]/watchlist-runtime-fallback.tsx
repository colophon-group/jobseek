import { Loader2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";

export function WatchlistRuntimeFallback() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      className="flex min-h-64 flex-col items-center justify-center gap-3 text-sm text-muted"
    >
      <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      <Trans id="myJobs.stats.loading" comment="Loading indicator">
        Loading…
      </Trans>
    </div>
  );
}
