"use client";

import { Trans } from "@lingui/react/macro";

// WCAG 4.1.3 (status messages): screen readers stay silent on visual-only
// skeleton placeholders unless we mark the container as a polite live region
// in a busy state. The visually-hidden child gives the SR an actual phrase
// to announce when the skeleton mounts (closes #3190).
export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      className="space-y-4"
    >
      <span className="sr-only">
        <Trans
          id="common.a11y.loadingResults"
          comment="Screen-reader announcement while a list of result cards is loading"
        >
          Loading results
        </Trans>
      </span>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          aria-hidden="true"
          className="animate-pulse rounded-md border border-divider bg-surface p-4"
        >
          <div className="flex items-center gap-3">
            <div className="size-8 rounded bg-border-soft" />
            <div className="h-4 w-32 rounded bg-border-soft" />
          </div>
          <div className="mt-3 h-3 w-40 rounded bg-border-soft" />
          <hr className="my-3 border-divider" />
          <div className="space-y-2">
            <div className="h-4 w-full rounded bg-border-soft" />
            <div className="h-4 w-3/4 rounded bg-border-soft" />
            <div className="h-4 w-5/6 rounded bg-border-soft" />
          </div>
        </div>
      ))}
    </div>
  );
}
