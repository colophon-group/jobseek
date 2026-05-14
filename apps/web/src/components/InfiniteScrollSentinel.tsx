"use client";

import { Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";

interface InfiniteScrollSentinelProps {
  // Accept any React ref shape — `useInfiniteScroll` returns a
  // callback ref (so the observer auto-attaches when the DOM element
  // appears) but older callers may pass a `useRef` object. `Ref<T>`
  // is the union type that satisfies the `<div ref={...}>` prop.
  sentinelRef: React.Ref<HTMLDivElement>;
  isLoading: boolean;
  /** "sm" for nested scroll containers, "md" (default) for page-level lists */
  size?: "sm" | "md";
  /**
   * Scroll axis. "vertical" (default) sets a fixed height; "horizontal"
   * sets a fixed width and `shrink-0` so the sentinel survives a flex
   * row without collapsing.
   */
  orientation?: "vertical" | "horizontal";
}

// WCAG 4.1.3 (status messages): the IntersectionObserver sentinel is
// visually a spinner, but used to be silent to screen readers. We wrap it in
// a polite live region so an SR announces "Loading more results" each time
// the sentinel scrolls into view and a new page starts streaming. The
// sentinel `<div>` itself carries an `aria-label` so the visual region has a
// name even when no spinner is rendered (closes #3190).
export function InfiniteScrollSentinel({
  sentinelRef,
  isLoading,
  size = "md",
  orientation = "vertical",
}: InfiniteScrollSentinelProps) {
  const { t } = useLingui();
  const axisSize =
    orientation === "horizontal"
      ? size === "sm"
        ? "w-6 shrink-0"
        : "w-8 shrink-0"
      : size === "sm"
        ? "h-6"
        : "h-8";
  const sentinelLabel = t({
    id: "common.a11y.loadingMoreResults",
    comment:
      "Aria label and screen-reader announcement on the infinite-scroll sentinel that triggers fetching the next page of results",
    message: "Loading more results",
  });
  return (
    <div
      ref={sentinelRef}
      role="status"
      aria-live="polite"
      aria-busy={isLoading}
      aria-label={sentinelLabel}
      className={`flex items-center justify-center ${axisSize}`}
      style={{ overflowAnchor: "none" }}
    >
      {isLoading && (
        <>
          <Loader2
            aria-hidden="true"
            size={size === "sm" ? 12 : 14}
            className="animate-spin text-muted"
          />
          <span className="sr-only">
            <Trans
              id="common.a11y.loadingMoreResults"
              comment="Aria label and screen-reader announcement on the infinite-scroll sentinel that triggers fetching the next page of results"
            >
              Loading more results
            </Trans>
          </span>
        </>
      )}
    </div>
  );
}
