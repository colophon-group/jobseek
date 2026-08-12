import { RefreshCw } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { getSession } from "@/lib/sessionCache";
import {
  getPopularWatchlists,
  getUserWatchlistsWithLimit,
} from "@/lib/services/watchlists";
import { logExternalError } from "@/lib/safe-external-error";
import { WatchlistsPage } from "./watchlists-page";

const WATCHLIST_LOAD_TIMEOUT_MS = 8_000;
const PUBLIC_WATCHLIST_PAGE_SIZE = 10;

async function loadWatchlists(locale: string) {
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

/**
 * Initial watchlist data is a read, so load it in the server tree instead
 * of calling a Server Action from a client effect. The latter left the
 * action promise unsettled in production even though Vercel recorded 200
 * responses, keeping this core page unusable after hydration (#5896).
 */
export async function WatchlistsLoader({ locale }: { locale: string }) {
  const session = await getSession();

  try {
    const [{ watchlists, limitReached }, popular] = await Promise.all([
      loadWatchlists(locale),
      // A cold precise-count pass can take longer than three seconds. Do not
      // abandon it here: this is the only mount read, so a timed-out result
      // would leave the public panel empty until the user hard-reloads.
      getPopularWatchlists({
        offset: 0,
        limit: PUBLIC_WATCHLIST_PAGE_SIZE,
        locale,
      }).catch((err) => {
        logExternalError(
          "error",
          { service: "typesense", operation: "load_popular_watchlists" },
          err,
        );
        return { watchlists: [], total: 0 };
      }),
    ]);
    return (
      <WatchlistsPage
        initialWatchlists={watchlists}
        initialPopularWatchlists={popular.watchlists}
        initialPopularTotal={popular.total}
        username={session?.user.username ?? null}
        limitReached={limitReached}
        locale={locale}
      />
    );
  } catch (err) {
    logExternalError("error", { service: "database", operation: "load_watchlists" }, err);
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="text-sm font-medium">
          <Trans id="watchlists.load.error" comment="Error shown when the watchlists overview cannot load after a retry">
            We couldn&apos;t load your watchlists.
          </Trans>
        </p>
        <a
          href={`/${locale}/watchlists`}
          className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border-soft px-3 py-2 text-sm font-medium transition-colors hover:bg-border-soft"
        >
          <RefreshCw size={14} aria-hidden="true" />
          <Trans id="watchlists.load.retry" comment="Button to retry loading the watchlists overview">
            Try again
          </Trans>
        </a>
      </div>
    );
  }
}
