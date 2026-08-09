"use client";

import { useEffect, useState } from "react";
import { Trans } from "@lingui/react/macro";
import { fetchWatchlistPageData, type WatchlistPageData } from "@/lib/actions/watchlist-page-data";
import { hasAnonJobLanguagesHint, hasLoggedInHint } from "@/lib/client-cookies";
import { WatchlistSkeleton } from "@/components/search/watchlist-skeleton";
import { WatchlistViewPage } from "./watchlist-view-page";
import { WatchlistNotFoundState } from "./watchlist-not-found";

type WatchlistContentProps = {
  lang: string;
  userSlug: string;
  watchlistSlug: string;
  /** Cache-safe anonymous data, or null when the server confirmed no access. */
  initialData?: WatchlistPageData | null;
  /** The server already resolved this specific viewer, so no mount fetch is needed. */
  viewerResolved?: boolean;
};

function WatchlistNotFound({ lang }: { lang: string }) {
  return (
    <WatchlistNotFoundState
      lang={lang}
      title={
        <Trans
          id="watchlist.notFound.title"
          comment="Heading shown when the watchlist URL doesn't resolve to a public watchlist"
        >
          Watchlist not found
        </Trans>
      }
      message={
        <Trans
          id="watchlist.notFound.body"
          comment="Body text for the watchlist-not-found page; explains the watchlist is either gone or private"
        >
          This watchlist does not exist or is not public.
        </Trans>
      }
      browseLabel={
        <Trans
          id="watchlist.notFound.browse"
          comment="Recovery action on the watchlist-not-found page"
        >
          Browse watchlists
        </Trans>
      }
    />
  );
}

export function WatchlistContent({
  lang,
  userSlug,
  watchlistSlug,
  initialData,
  viewerResolved = false,
}: WatchlistContentProps) {
  const [data, setData] = useState<WatchlistPageData | null | "not-found">(
    initialData === null ? "not-found" : (initialData ?? null),
  );

  useEffect(() => {
    window.scrollTo(0, 0);
    if (viewerResolved) {
      setData(initialData ?? "not-found");
      return;
    }

    const needsPersonalizedFetch =
      hasLoggedInHint() ||
      hasAnonJobLanguagesHint() ||
      initialData === undefined;
    if (!needsPersonalizedFetch) {
      setData(initialData);
      return;
    }

    setData(null);
    fetchWatchlistPageData({ userSlug, watchlistSlug, locale: lang }).then((result) => {
      setData(result ?? "not-found");
    });
  }, [initialData, lang, userSlug, viewerResolved, watchlistSlug]);

  if (data === null) return <WatchlistSkeleton />;
  if (data === "not-found") return <WatchlistNotFound lang={lang} />;

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
