import { SkeletonCards } from "@/components/search/skeleton-card";

export function WatchlistSkeleton() {
  return (
    <div className="space-y-6">
      {/* Watchlist header */}
      <div className="space-y-3">
        <div className="h-6 w-48 animate-pulse rounded bg-border-soft" />
        <div className="h-3 w-32 animate-pulse rounded bg-border-soft" />
      </div>
      {/* Company pills */}
      <div className="flex flex-wrap gap-2">
        <div className="h-7 w-20 animate-pulse rounded-full bg-border-soft" />
        <div className="h-7 w-24 animate-pulse rounded-full bg-border-soft" />
        <div className="h-7 w-16 animate-pulse rounded-full bg-border-soft" />
      </div>
      <SkeletonCards count={3} />
    </div>
  );
}
