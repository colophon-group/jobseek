import { SkeletonCards } from "@/components/search/skeleton-card";

export function ExploreSkeleton() {
  return (
    <div className="space-y-6">
      {/* Toolbar placeholder */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="h-8 w-24 animate-pulse rounded-md bg-border-soft" />
        <div className="h-8 w-20 animate-pulse rounded-md bg-border-soft" />
        <div className="h-8 w-28 animate-pulse rounded-md bg-border-soft" />
      </div>
      <SkeletonCards count={3} />
    </div>
  );
}
