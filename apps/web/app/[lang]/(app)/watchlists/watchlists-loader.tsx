import { RefreshCw } from "lucide-react";
import type { Locale } from "@/lib/i18n";
import { getUserWatchlistsWithLimit } from "@/lib/services/watchlists";
import { logExternalError } from "@/lib/safe-external-error";
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

/** The overview deliberately loads only list metadata; detail has its own route. */
export async function WatchlistsLoader({
  locale,
  errorLabel,
  retryLabel,
}: {
  locale: Locale;
  errorLabel: string;
  retryLabel: string;
}) {
  try {
    const { watchlists, limitReached } = await loadWatchlists(locale);
    return (
      <WatchlistsPage
        initialWatchlists={watchlists}
        limitReached={limitReached}
        locale={locale}
      />
    );
  } catch (err) {
    logExternalError("error", { service: "database", operation: "load_watchlists" }, err);
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="text-sm font-medium">
          {errorLabel}
        </p>
        <a
          href={`/${locale}/watchlists`}
          className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border-soft px-3 py-2 text-sm font-medium transition-colors hover:bg-border-soft"
        >
          <RefreshCw size={14} aria-hidden="true" />
          {retryLabel}
        </a>
      </div>
    );
  }
}
