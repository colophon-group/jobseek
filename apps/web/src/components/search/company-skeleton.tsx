import { SkeletonCards } from "@/components/search/skeleton-card";

export function CompanySkeleton() {
  return (
    <div className="space-y-6">
      {/* Company header */}
      <div className="flex items-center gap-4">
        <div className="size-12 animate-pulse rounded-lg bg-border-soft" />
        <div className="space-y-2">
          <div className="h-5 w-40 animate-pulse rounded bg-border-soft" />
          <div className="h-3 w-24 animate-pulse rounded bg-border-soft" />
        </div>
      </div>
      {/* Toolbar placeholder */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="h-8 w-24 animate-pulse rounded-md bg-border-soft" />
        <div className="h-8 w-20 animate-pulse rounded-md bg-border-soft" />
        <div className="h-8 w-28 animate-pulse rounded-md bg-border-soft" />
      </div>
      <SkeletonCards count={4} />
    </div>
  );
}
