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
      locale={lang}
      resolvedLocations={data.resolvedLocations}
      resolvedOccupations={data.resolvedOccupations}
      resolvedSeniorities={data.resolvedSeniorities}
      resolvedTechnologies={data.resolvedTechnologies}
    />
  );
}
