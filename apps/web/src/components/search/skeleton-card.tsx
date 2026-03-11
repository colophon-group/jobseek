"use client";

export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
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
