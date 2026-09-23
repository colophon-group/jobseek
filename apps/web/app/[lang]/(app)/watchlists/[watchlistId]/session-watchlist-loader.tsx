"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import { useSession } from "@/components/providers/SessionProvider";
import { getSessionWatchlistPageData } from "@/lib/actions/session-watchlists";
import type { Locale } from "@/lib/i18n";
import {
  readPendingWatchlists,
  updatePendingWatchlist,
} from "@/lib/pending-watchlist";
import type { WatchlistPageData } from "@/lib/services/watchlist-page-data";
import { WatchlistViewPage } from "@/components/watchlist/watchlist-view-page";

export function SessionWatchlistLoader({
  locale,
  watchlistId,
  overviewLabel,
}: {
  locale: Locale;
  watchlistId: string;
  overviewLabel: string;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const { isLoggedIn, isPending } = useSession();
  const [data, setData] = useState<WatchlistPageData | null>(null);
  const [failed, setFailed] = useState(false);
  const overviewHref = `/${locale}/watchlists`;

  useEffect(() => {
    if (isPending) return;
    if (isLoggedIn) {
      router.replace(overviewHref);
      return;
    }

    const entry = readPendingWatchlists().find((candidate) => candidate.id === watchlistId);
    if (!entry) {
      router.replace(overviewHref);
      return;
    }

    let cancelled = false;
    setFailed(false);
    void getSessionWatchlistPageData({
      sessionWatchlistId: watchlistId,
      locale,
      intent: entry.intent,
    }).then((result) => {
      if (cancelled) return;
      if ("error" in result) {
        setFailed(true);
        return;
      }
      if (entry.intent.kind === "clone") {
        updatePendingWatchlist(watchlistId, {
          kind: "create",
          draft: result.draft,
        });
      }
      setData(result.data);
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });

    return () => {
      cancelled = true;
    };
  }, [isLoggedIn, isPending, locale, overviewHref, router, watchlistId]);

  if (!data) {
    return (
      <div className="space-y-5">
        <Link
          href={overviewHref}
          prefetch={false}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <ChevronLeft size={16} aria-hidden="true" />
          {overviewLabel}
        </Link>
        {failed ? (
          <p className="py-16 text-center text-sm text-muted" role="alert">
            {t({
              id: "watchlists.pending.loadError",
              comment: "Error shown when a session watchlist cannot load",
              message: "We couldn't load your watchlist.",
            })}
          </p>
        ) : (
          <div className="flex items-center justify-center py-24" role="status">
            <div className="size-8 rounded-full border-4 border-muted border-t-primary motion-safe:animate-spin" />
            <span className="sr-only">
              {t({
                id: "watchlists.pending.loading",
                comment: "Accessible loading label for an anonymous watchlist stored in browser state",
                message: "Loading watchlist…",
              })}
            </span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Link
        href={overviewHref}
        prefetch={false}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none"
      >
        <ChevronLeft size={16} aria-hidden="true" />
        {overviewLabel}
      </Link>
      <WatchlistViewPage
        data={data}
        sessionWatchlistId={watchlistId}
        locale={locale}
      />
    </div>
  );
}
