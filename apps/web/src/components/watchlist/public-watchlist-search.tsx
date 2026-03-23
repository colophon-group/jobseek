"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import Link from "next/link";
import { Search, Loader2, Copy } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import {
  searchPublicWatchlists,
  getPopularWatchlists,
  type PublicWatchlistEntry,
} from "@/lib/actions/watchlists";

export function PublicWatchlistSearch() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PublicWatchlistEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [mode, setMode] = useState<"popular" | "search">("popular");
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Load popular watchlists on mount
  useEffect(() => {
    setLoading(true);
    getPopularWatchlists({ offset: 0, limit: 10 })
      .then(({ watchlists, total }) => {
        setResults(watchlists);
        setTotal(total);
        setMode("popular");
      })
      .finally(() => setLoading(false));
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) {
      // Revert to popular
      setLoading(true);
      getPopularWatchlists({ offset: 0, limit: 10 })
        .then(({ watchlists, total }) => {
          setResults(watchlists);
          setTotal(total);
          setMode("popular");
        })
        .finally(() => setLoading(false));
      return;
    }
    setLoading(true);
    try {
      const { watchlists, total } = await searchPublicWatchlists({
        query: q,
        offset: 0,
        limit: 10,
      });
      setResults(watchlists);
      setTotal(total);
      setMode("search");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => doSearch(query), query.length > 0 ? 300 : 0);
    return () => clearTimeout(timerRef.current);
  }, [query, doSearch]);

  return (
    <div>
      <h2 className="mb-3 text-sm font-semibold">
        <Trans id="watchlists.explore.publicTitle" comment="Section title for discovering public watchlists">
          Discover public watchlists
        </Trans>
      </h2>

      <div className="mb-4 flex items-center gap-2 rounded-md border border-border-soft px-3 py-2">
        <Search size={14} className="shrink-0 text-muted" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t({
            id: "watchlists.explore.searchPlaceholder",
            comment: "Placeholder for public watchlist search input",
            message: "Search watchlists...",
          })}
          className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
        />
        {loading && <Loader2 size={14} className="animate-spin text-muted" />}
      </div>

      {results.length > 0 && (
        <div className="space-y-2">
          {results.map((wl) => (
            <Link
              key={wl.id}
              href={wl.ownerUsername ? lp(`/${wl.ownerUsername}/${wl.slug}`) : "#"}
              prefetch={false}
              className="flex items-center gap-3 rounded-md border border-border-soft p-3 transition-colors hover:bg-border-soft"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{wl.title}</p>
                {wl.description && (
                  <p className="line-clamp-1 text-xs text-muted">{wl.description}</p>
                )}
                <p className="text-xs text-muted">
                  @{wl.ownerUsername ?? t({ id: "watchlists.explore.unknownUser", comment: "Fallback username for watchlist owner", message: "user" })} · {wl.activeJobCount} {wl.activeJobCount === 1
                    ? t({ id: "watchlists.explore.jobSingular", comment: "Singular job count in public watchlist", message: "job" })
                    : t({ id: "watchlists.explore.jobPlural", comment: "Plural job count in public watchlist", message: "jobs" })}
                  {wl.mirrorCount > 0 && (
                    <>
                      {" · "}
                      <span className="inline-flex items-center gap-0.5">
                        <Copy size={10} />
                        {wl.mirrorCount} {wl.mirrorCount === 1
                          ? t({ id: "watchlists.explore.mirrorSingular", comment: "Singular mirror count", message: "mirror" })
                          : t({ id: "watchlists.explore.mirrorPlural", comment: "Plural mirror count", message: "mirrors" })}
                      </span>
                    </>
                  )}
                </p>
              </div>
            </Link>
          ))}

          {total > results.length && (
            <p className="text-center text-xs text-muted">
              <Trans id="watchlists.explore.moreCount" comment="Count of additional watchlists not shown">
                {total - results.length} more
              </Trans>
            </p>
          )}
        </div>
      )}

      {!loading && results.length === 0 && mode === "search" && (
        <p className="text-center text-sm text-muted">
          <Trans id="watchlists.explore.noResults" comment="No results message when searching public watchlists">
            No watchlists found.
          </Trans>
        </p>
      )}
    </div>
  );
}
