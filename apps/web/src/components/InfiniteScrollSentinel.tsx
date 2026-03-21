"use client";

import { Loader2 } from "lucide-react";

interface InfiniteScrollSentinelProps {
  sentinelRef: React.RefObject<HTMLDivElement | null>;
  isLoading: boolean;
  /** "sm" for nested scroll containers, "md" (default) for page-level lists */
  size?: "sm" | "md";
}

export function InfiniteScrollSentinel({
  sentinelRef,
  isLoading,
  size = "md",
}: InfiniteScrollSentinelProps) {
  return (
    <div
      ref={sentinelRef}
      className={`flex items-center justify-center ${size === "sm" ? "h-6" : "h-8"}`}
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
