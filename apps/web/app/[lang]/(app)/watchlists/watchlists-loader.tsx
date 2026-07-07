"use client";

import { useEffect, useState } from "react";
import { getUserWatchlistsWithLimit, type WatchlistSummary } from "@/lib/actions/watchlists";
import { useSession } from "@/components/providers/SessionProvider";
import { WatchlistsPage } from "./watchlists-page";

type WatchlistsData = {
  watchlists: WatchlistSummary[];
  username: string | null;
  limitReached: boolean;
};

export function WatchlistsLoader({ locale }: { locale: string }) {
  const { user, isPending } = useSession();
  const [data, setData] = useState<WatchlistsData | null>(null);

  useEffect(() => {
    if (isPending) return;
    if (!user) {
      setData({ watchlists: [], username: null, limitReached: true });
      return;
    }
    // Issue #3036: previously hardcoded ``limitReached: false`` here,
    // which left ``CreateWatchlistCard`` permanently enabled even for
    // users at the plan limit. Server now returns both so the card
    // surfaces its disabled state + upgrade modal correctly.
    getUserWatchlistsWithLimit(locale).then(({ watchlists, limitReached }) => {
      setData({
        watchlists,
        username: user.username ?? null,
        limitReached,
      });
    });
  }, [user, isPending, locale]);

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
