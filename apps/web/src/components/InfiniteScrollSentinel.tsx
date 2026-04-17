"use client";

import { Loader2 } from "lucide-react";

interface InfiniteScrollSentinelProps {
  sentinelRef: React.RefObject<HTMLDivElement | null>;
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

export function InfiniteScrollSentinel({
  sentinelRef,
  isLoading,
  size = "md",
  orientation = "vertical",
}: InfiniteScrollSentinelProps) {
  const axisSize =
    orientation === "horizontal"
      ? size === "sm"
        ? "w-6 shrink-0"
        : "w-8 shrink-0"
      : size === "sm"
        ? "h-6"
        : "h-8";
  return (
    <div
      ref={sentinelRef}
      className={`flex items-center justify-center ${axisSize}`}
      style={{ overflowAnchor: "none" }}
    >
      {isLoading && (
        <Loader2
          size={size === "sm" ? 12 : 14}
          className="animate-spin text-muted"
        />
      )}
    </div>
  );
}
