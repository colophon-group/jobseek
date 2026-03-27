"use client";

import { useEffect, useState } from "react";
import { getUserWatchlists, type WatchlistSummary } from "@/lib/actions/watchlists";
import { useSession } from "@/components/SessionProvider";
import { WatchlistsPage } from "./watchlists-page";

type WatchlistsData = {
  watchlists: WatchlistSummary[];
  username: string | null;
  limitReached: boolean;
};

export function WatchlistsLoader({ locale: _locale }: { locale: string }) {
  const { user, isPending } = useSession();
  const [data, setData] = useState<WatchlistsData | null>(null);

  useEffect(() => {
    if (isPending) return;
    if (!user) {
      setData({ watchlists: [], username: null, limitReached: true });
      return;
    }
    getUserWatchlists().then((watchlists) => {
      setData({
        watchlists,
        username: user.username ?? null,
        limitReached: false,
      });
    });
  }, [user, isPending]);

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
