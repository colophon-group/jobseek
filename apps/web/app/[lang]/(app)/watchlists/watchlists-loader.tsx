"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { getUserWatchlistsWithLimit, type WatchlistSummary } from "@/lib/actions/watchlists";
import { useSession } from "@/components/providers/SessionProvider";
import { WatchlistsPage } from "./watchlists-page";

type WatchlistsData = {
  watchlists: WatchlistSummary[];
  username: string | null;
  limitReached: boolean;
};

const WATCHLIST_LOAD_TIMEOUT_MS = 8_000;

async function loadWatchlistsWithRetry(locale: string) {
  async function attempt() {
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        getUserWatchlistsWithLimit(locale),
        new Promise<never>((_, reject) => {
          timeoutId = setTimeout(
            () => reject(new Error("Watchlists request timed out")),
            WATCHLIST_LOAD_TIMEOUT_MS,
          );
        }),
      ]);
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
  }

  try {
    return await attempt();
  } catch (err) {
    console.warn("[watchlists] initial load failed, retrying once", err);
    await new Promise((resolve) => setTimeout(resolve, 250));
    return attempt();
  }
}

export function WatchlistsLoader({ locale }: { locale: string }) {
  const { user, isPending } = useSession();
  const [data, setData] = useState<WatchlistsData | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const userId = user?.id;
  const username = user?.username ?? null;

  useEffect(() => {
    let cancelled = false;
    if (isPending) return;
    setLoadFailed(false);
    setData(null);

    if (!userId) {
      setData({ watchlists: [], username: null, limitReached: true });
      return;
    }
    // Issue #3036: previously hardcoded ``limitReached: false`` here,
    // which left ``CreateWatchlistCard`` permanently enabled even for
    // users at the plan limit. Server now returns both so the card
    // surfaces its disabled state + upgrade modal correctly.
    loadWatchlistsWithRetry(locale)
      .then(({ watchlists, limitReached }) => {
        if (cancelled) return;
        setData({
          watchlists,
          username,
          limitReached,
        });
      })
      .catch((err) => {
        console.error("[watchlists] load failed twice", err);
        if (!cancelled) setLoadFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [userId, username, isPending, locale, retryKey]);

  if (loadFailed) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="text-sm font-medium">
          <Trans id="watchlists.load.error" comment="Error shown when the watchlists overview cannot load after a retry">
            We couldn&apos;t load your watchlists.
          </Trans>
        </p>
        <button
          type="button"
          onClick={() => setRetryKey((value) => value + 1)}
          className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border-soft px-3 py-2 text-sm font-medium transition-colors hover:bg-border-soft"
        >
          <RefreshCw size={14} aria-hidden="true" />
          <Trans id="watchlists.load.retry" comment="Button to retry loading the watchlists overview">
            Try again
          </Trans>
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return (
    <WatchlistsPage
      initialWatchlists={data.watchlists}
      username={data.username}
      limitReached={data.limitReached}
    />
  );
}
