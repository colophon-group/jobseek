"use client";

import { useEffect, useState } from "react";
import { fetchWatchlistPageData, type WatchlistPageData } from "@/lib/actions/watchlist-page-data";
import { WatchlistSkeleton } from "@/components/search/watchlist-skeleton";
import { WatchlistViewPage } from "./watchlist-view-page";

type WatchlistContentProps = {
  lang: string;
  userSlug: string;
  watchlistSlug: string;
};

function WatchlistNotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">Watchlist not found</h1>
      <p className="mt-2 text-muted">This watchlist does not exist or is not public.</p>
    </div>
  );
}

export function WatchlistContent({ lang, userSlug, watchlistSlug }: WatchlistContentProps) {
  const [data, setData] = useState<WatchlistPageData | null | "not-found">(null);

  useEffect(() => {
    setData(null);
    // No `window.scrollTo(0, 0)` here — Next handles scroll on
    // navigation natively, and the previous unconditional reset
    // fired on every mount of this component. In edge cases (HMR
    // in dev, Suspense reconnection on `?show=` changes from
    // `useSearchParams()`, etc.) that re-mount fires after the
    // user has already scrolled into the list, snapping them
    // back to the top. See #3028.
    fetchWatchlistPageData({ userSlug, watchlistSlug, locale: lang }).then((result) => {
      setData(result ?? "not-found");
    });
  }, [lang, userSlug, watchlistSlug]);

  if (data === null) return <WatchlistSkeleton />;
  if (data === "not-found") return <WatchlistNotFound />;

  return (
    <WatchlistViewPage
      detail={data.detail}
      isOwner={data.isOwner}
      isPaidPlan={data.isPaidPlan}
      limitReached={data.limitReached}
      initialPostings={data.postings}
      initialTotal={data.total}
      yearTotal={data.yearTotal}
      locale={lang}
      resolvedLocations={data.resolvedLocations}
      resolvedOccupations={data.resolvedOccupations}
      resolvedSeniorities={data.resolvedSeniorities}
      resolvedTechnologies={data.resolvedTechnologies}
      jobLanguages={data.jobLanguages}
      languages={data.languages}
    />
  );
}
