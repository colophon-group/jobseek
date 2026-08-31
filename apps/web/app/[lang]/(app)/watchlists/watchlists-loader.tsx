import { RefreshCw } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { cookies } from "next/headers";
import { getSession } from "@/lib/sessionCache";
import {
  getOwnedWatchlistById,
  getUserWatchlistsWithLimit,
} from "@/lib/services/watchlists";
import { buildWatchlistPageData } from "@/lib/services/watchlist-page-data";
import { getPreferences } from "@/lib/actions/preferences";
import { getUserPlan, PLAN_LIMITS } from "@/lib/plans";
import { logExternalError } from "@/lib/safe-external-error";
import {
  WATCHLIST_SELECTION_COOKIE,
  decodeWatchlistSelection,
} from "@/lib/watchlist-selection";
import { WatchlistsPage } from "./watchlists-page";

const WATCHLIST_LOAD_TIMEOUT_MS = 8_000;

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
  const [session, cookieStore] = await Promise.all([getSession(), cookies()]);

  try {
    const { watchlists, limitReached } = await loadWatchlists(locale);
    const rawSelection = cookieStore.get(WATCHLIST_SELECTION_COOKIE)?.value;

    if (!session) {
      return (
        <WatchlistsPage
          initialWatchlists={[]}
          initialPageData={null}
          selectionSync={rawSelection ? "clear" : "none"}
          limitReached
          locale={locale}
        />
      );
    }

    const hintedId = decodeWatchlistSelection(
      rawSelection,
      session.user.id,
    );
    let detail = hintedId
      ? await getOwnedWatchlistById(hintedId, session.user.id)
      : null;
    let selectionSync: "none" | "replace" | "clear" =
      detail ? "none" : "clear";

    if (!detail) {
      // The list query is deterministically ordered by last access, creation,
      // then id. Re-check candidates through the exact owner predicate so a
      // concurrent deletion cannot promote a stale overview row.
      for (const candidate of watchlists) {
        detail = await getOwnedWatchlistById(candidate.id, session.user.id);
        if (detail) {
          selectionSync = "replace";
          break;
        }
      }
    }

    const initialPageData = detail
      ? await Promise.all([
          getUserPlan(session.user.id),
          getPreferences(),
        ]).then(([plan, preferences]) =>
          buildWatchlistPageData({
            detail,
            locale,
            isOwner: true,
            isPaidPlan: PLAN_LIMITS[plan].canReceiveAlerts,
            limitReached,
            jobLanguages: preferences?.jobLanguages ?? [],
            publicSnapshot: false,
          }),
        )
      : null;

    return (
      <WatchlistsPage
        initialWatchlists={watchlists}
        initialPageData={initialPageData}
        selectionSync={selectionSync}
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
