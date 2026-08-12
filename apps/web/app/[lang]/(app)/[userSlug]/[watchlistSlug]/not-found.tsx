"use client";

import { Trans } from "@lingui/react/macro";
import { useParams } from "next/navigation";
import { WatchlistNotFoundState } from "./watchlist-not-found";

/**
 * The same privacy-safe recovery UI is used for missing watchlists and
 * private watchlists viewed by a non-owner, so the response reveals nothing
 * about whether a resource exists.
 */
export default function WatchlistNotFound() {
  const { lang } = useParams<{ lang: string }>();

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
