"use client";

import { Trans } from "@lingui/react/macro";
import { SkeletonCards } from "@/components/search/skeleton-card";

export function ExploreSkeleton() {
  return (
    <div className="space-y-6">
      {/*
        Route-level loading.tsx is the cached Explore document's initial HTML
        boundary. Keep the page heading inside that boundary so no-JavaScript
        readers and assistive technology receive a localized document outline
        before the cached result payload resumes. SearchPage renders the same
        heading after Suspense replaces this fallback, so there is exactly one
        H1 in either state.
      */}
      <h1 className="sr-only">
        <Trans
          id="explore.h1"
          comment="Hidden page H1 for /explore — screen-reader landmark"
        >
          Explore Jobs
        </Trans>
      </h1>
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
