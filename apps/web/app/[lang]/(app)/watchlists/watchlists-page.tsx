"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSession } from "@/components/providers/SessionProvider";
import type {
  UserWatchlistActivityPreview,
  UserWatchlistOverview,
  WatchlistFilters,
} from "@/lib/actions/watchlists";
import {
  createWatchlist,
  createWatchlistFromHandoff,
  copySharedWatchlist,
  deleteWatchlist,
  shareWatchlist,
} from "@/lib/actions/watchlists";
import {
  WatchlistCard,
  CreateWatchlistCard,
} from "@/components/watchlist/watchlist-card";
import { ScrollFade } from "@/components/ui/scroll-fade";
import {
  parseEmploymentTypeParam,
  parseWorkModeParam,
} from "@/lib/search/query-params";
import { withAuthReturnPath } from "@/lib/auth-return";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { copyTextToClipboard } from "@/lib/copy-text-to-clipboard";
import { getSessionWatchlistActivityPreviews } from "@/lib/actions/session-watchlists";
import {
  PENDING_WATCHLIST_LIMIT,
  clearPendingWatchlist,
  readPendingWatchlists,
  removePendingWatchlist,
  stagePendingWatchlistEntry,
  type PendingWatchlistEntry,
  type PendingWatchlistIntent,
} from "@/lib/pending-watchlist";

function commaSeparatedValues(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

const SESSION_WATCHLIST_DATE = new Date(0).toISOString();

function sessionEntryOverview(
  entry: PendingWatchlistEntry,
  cloneFallbackTitle: string,
): UserWatchlistOverview {
  const { id, intent } = entry;
  return {
    id,
    slug: id,
    title: intent.kind === "create"
      ? intent.draft.title
      : intent.title ?? cloneFallbackTitle,
    description: intent.kind === "create"
      ? intent.draft.description ?? null
      : null,
    isShared: false,
    alertsEnabled: false,
    companyCount: intent.kind === "create" ? intent.draft.companyIds.length : 0,
    activeJobCount: null,
    lastAccessedAt: SESSION_WATCHLIST_DATE,
    createdAt: SESSION_WATCHLIST_DATE,
  };
}

async function loadAccountActivityPreviews(
  locale: string,
  signal: AbortSignal,
): Promise<Record<string, UserWatchlistActivityPreview>> {
  const response = await fetch(
    `/api/web/watchlists/counts?locale=${encodeURIComponent(locale)}`,
    { cache: "no-store", signal },
  );
  if (!response.ok) {
    throw new Error(`Watchlist previews failed: ${response.status}`);
  }
  const result = await response.json() as {
    previews?: Record<string, UserWatchlistActivityPreview>;
  };
  return result.previews ?? {};
}

export function WatchlistsPage({
  initialWatchlists,
  limitReached,
  locale,
}: {
  initialWatchlists: UserWatchlistOverview[];
  limitReached: boolean;
  locale: string;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { isLoggedIn, isPending } = useSession();
  const currencyRates = useSalaryRates();
  const searchParams = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [pendingWatchlists, setPendingWatchlists] = useState<PendingWatchlistEntry[]>([]);
  const [activityById, setActivityById] = useState<
    Record<string, UserWatchlistActivityPreview>
  >({});
  const [activityPending, setActivityPending] = useState(
    () => initialWatchlists.length > 0,
  );
  const handoffAttemptedRef = useRef(false);
  const defaultWatchlistTitle = t({
    id: "watchlists.defaultTitle",
    comment: "Default title assigned to a newly created watchlist",
    message: "New watchlist",
  });

  useEffect(() => {
    if (isPending) return;
    const entries = isLoggedIn ? [] : readPendingWatchlists();
    if (!isLoggedIn) setPendingWatchlists(entries);
    const watchlistCount = isLoggedIn ? initialWatchlists.length : entries.length;
    setActivityById({});
    setActivityPending(watchlistCount > 0);
    if (watchlistCount === 0) return;

    const controller = new AbortController();
    let disposed = false;
    const timeoutId = isLoggedIn
      ? setTimeout(() => controller.abort(), 12_000)
      : undefined;
    const previews = isLoggedIn
      ? loadAccountActivityPreviews(locale, controller.signal)
      : getSessionWatchlistActivityPreviews({ entries, locale }).then(
          (result) => "previews" in result ? result.previews ?? {} : {},
        );

    void previews
      .then((nextActivity) => {
        if (!disposed) setActivityById(nextActivity);
      })
      .catch(() => {
        // Watchlists remain navigable when optional live activity fails.
      })
      .finally(() => {
        if (timeoutId) clearTimeout(timeoutId);
        if (!disposed) setActivityPending(false);
      });

    return () => {
      disposed = true;
      if (timeoutId) clearTimeout(timeoutId);
      controller.abort();
    };
  }, [initialWatchlists, isLoggedIn, isPending, locale]);

  function navigateToCreatedWatchlist(
    id: string,
    navigation: "push" | "replace",
  ) {
    const destination = lp(`/watchlists/${id}`);
    if (navigation === "replace") router.replace(destination);
    else router.push(destination);
  }

  async function handleCreate(
    prefill?: {
      title?: string;
      description?: string;
      filters?: WatchlistFilters;
      companySlugs?: string[];
    },
    navigation: "push" | "replace" = "push",
  ) {
    if (creating || !isLoggedIn || limitReached) return;
    setCreating(true);
    setCreateError("");
    try {
      const result = prefill?.companySlugs !== undefined
        ? await createWatchlistFromHandoff({
            title: prefill.title || defaultWatchlistTitle,
            description: prefill.description,
            companySlugs: prefill.companySlugs,
            filters: prefill.filters,
          })
        : await createWatchlist({
            title: prefill?.title || defaultWatchlistTitle,
            description: prefill?.description,
            companyIds: [],
            filters: prefill?.filters,
            isPublic: false,
          });
      if ("error" in result) {
        setCreateError(t({
          id: "watchlists.createFailed",
          comment: "Error shown when a new watchlist cannot be created",
          message: "Could not create this watchlist.",
        }));
        return;
      }

      navigateToCreatedWatchlist(result.id, navigation);
    } catch {
      setCreateError(t({
        id: "watchlists.createFailed",
        comment: "Error shown when a new watchlist cannot be created",
        message: "Could not create this watchlist.",
      }));
    } finally {
      setCreating(false);
    }
  }

  async function handleShare(watchlistId: string) {
    const result = await shareWatchlist(watchlistId);
    if ("error" in result) throw new Error(result.error);
    await copyTextToClipboard(result.url);
  }

  async function handleDelete(watchlistId: string) {
    const result = await deleteWatchlist(watchlistId);
    if (!result.ok) throw new Error("delete_failed");
    router.refresh();
  }

  const runWatchlistHandoff = useEffectEvent(
    (prefill: {
      title: string;
      description?: string;
      filters?: WatchlistFilters;
      companySlugs: string[];
    }) => handleCreate(prefill, "replace"),
  );

  const runPendingWatchlistHandoff = useEffectEvent(
    async (entries: PendingWatchlistEntry[]) => {
      setCreating(true);
      setCreateError("");
      const availableSlots = Math.max(0, 10 - initialWatchlists.length);
      const importable = entries.slice(0, availableSlots);
      const overflow = entries.slice(availableSlots);
      const overflowed = overflow.length > 0;
      const createdIds: string[] = [];
      try {
        for (let index = 0; index < importable.length; index += 1) {
          const { id, intent } = importable[index];
          let result;
          try {
            result = intent.kind === "clone"
              ? await copySharedWatchlist(intent.watchlistId)
              : await createWatchlist(intent.draft);
          } catch {
            setCreateError(t({
              id: "watchlists.createFailed",
              comment: "Error shown when a new watchlist cannot be created",
              message: "Could not create this watchlist.",
            }));
            return;
          }
          if ("error" in result) {
            if (result.error !== "limit_reached") {
              setCreateError(t({
                id: "watchlists.createFailed",
                comment: "Error shown when a new watchlist cannot be created",
                message: "Could not create this watchlist.",
              }));
              return;
            }
            clearPendingWatchlist();
            setCreateError(t({
              id: "watchlists.pending.limitDiscarded",
              comment: "Notice that signed-out watchlists were discarded because the account is full",
              message: "Maximum of 10 watchlists reached. Extra saved watchlists were discarded.",
            }));
            break;
          }
          removePendingWatchlist(id);
          createdIds.push(result.id);
        }

        if (overflowed) {
          for (const entry of overflow) removePendingWatchlist(entry.id);
          setCreateError(t({
            id: "watchlists.pending.limitDiscarded",
            comment: "Notice that signed-out watchlists were discarded because the account is full",
            message: "Maximum of 10 watchlists reached. Extra saved watchlists were discarded.",
          }));
        }
        if (createdIds.length === 1) {
          navigateToCreatedWatchlist(createdIds[0], "replace");
        } else if (createdIds.length > 1) {
          router.refresh();
        }
      } catch {
        setCreateError(t({
          id: "watchlists.createFailed",
          comment: "Error shown when a new watchlist cannot be created",
          message: "Could not create this watchlist.",
        }));
      } finally {
        setCreating(false);
      }
    },
  );

  useEffect(() => {
    if (isPending || handoffAttemptedRef.current) return;

    const pendingWatchlists = isLoggedIn ? readPendingWatchlists() : [];
    if (pendingWatchlists.length > 0) {
      handoffAttemptedRef.current = true;
      if (limitReached) {
        clearPendingWatchlist();
        setCreateError(t({
          id: "watchlists.pending.limitDiscarded",
          comment: "Notice that signed-out watchlists were discarded because the account is full",
          message: "Maximum of 10 watchlists reached. Extra saved watchlists were discarded.",
        }));
        return;
      }
      void runPendingWatchlistHandoff(pendingWatchlists);
      return;
    }

    const title = searchParams.get("title");
    if (!title) return;

    handoffAttemptedRef.current = true;
    if (!isLoggedIn || limitReached) return;

    const q = searchParams.get("q");
    const loc = searchParams.get("loc");
    const occ = searchParams.get("occ");
    const sen = searchParams.get("sen");
    const tech = searchParams.get("tech");
    const sal = searchParams.get("sal");
    const exp = searchParams.get("exp");
    const salcur = searchParams.get("salcur");
    const workMode = parseWorkModeParam(searchParams.get("wm"));
    const employmentType = parseEmploymentTypeParam(searchParams.get("etype"));
    const companySlugs = commaSeparatedValues(searchParams.get("companies"));

    const filters: WatchlistFilters = {};
    if (q) filters.keywords = commaSeparatedValues(q);
    if (loc) filters.locationSlugs = commaSeparatedValues(loc);
    if (occ) filters.occupationSlugs = commaSeparatedValues(occ);
    if (sen) filters.senioritySlugs = commaSeparatedValues(sen);
    if (tech) filters.technologySlugs = commaSeparatedValues(tech);
    if (workMode.length > 0) filters.workMode = workMode;
    if (employmentType.length > 0) filters.employmentType = employmentType;
    if (sal) {
      const [minStr, maxStr] = sal.split("-");
      const salaryMinEur = minStr ? parseInt(minStr, 10) : undefined;
      const salaryMaxEur = maxStr ? parseInt(maxStr, 10) : undefined;
      const rate = salcur && salcur !== "EUR"
        ? currencyRates.find((candidate) => candidate.currency === salcur)?.toEur
        : 1;
      if (rate && rate > 0) {
        filters.salaryCurrency = salcur ?? "EUR";
        if (salaryMinEur !== undefined) filters.salaryMin = Math.round(salaryMinEur / rate);
        if (salaryMaxEur !== undefined) filters.salaryMax = Math.round(salaryMaxEur / rate);
      } else {
        // `sal` in the public handoff contract is EUR. If the requested
        // display currency is unsupported, preserve the filter's meaning.
        filters.salaryCurrency = "EUR";
        if (salaryMinEur !== undefined) filters.salaryMin = salaryMinEur;
        if (salaryMaxEur !== undefined) filters.salaryMax = salaryMaxEur;
      }
    } else if (salcur) {
      filters.salaryCurrency = salcur;
    }
    if (exp) {
      const [minStr, maxStr] = exp.split("-");
      if (minStr) filters.experienceMin = parseInt(minStr, 10);
      if (maxStr) filters.experienceMax = parseInt(maxStr, 10);
    }

    void runWatchlistHandoff({
      title,
      description: searchParams.get("description") ?? undefined,
      filters: Object.keys(filters).length > 0 ? filters : undefined,
      companySlugs,
    }).catch(() => {
      // Keep the handoff URL intact after a terminal action/database failure.
    });
  }, [currencyRates, isLoggedIn, isPending, limitReached, searchParams, t]);

  function stageBlankWatchlist() {
    const intent: PendingWatchlistIntent = {
      kind: "create",
      draft: {
        title: defaultWatchlistTitle,
        companyIds: [],
        filters: { anyCompany: true },
        isPublic: false,
      },
    };
    const entry = stagePendingWatchlistEntry(intent);
    if (entry) {
      setPendingWatchlists(readPendingWatchlists());
      router.push(lp(`/watchlists/${entry.id}`));
      return;
    }
    router.push(loginHref);
  }

  function discardPendingWatchlist(id: string) {
    removePendingWatchlist(id);
    setPendingWatchlists(readPendingWatchlists());
    setActivityById((current) => {
      const { [id]: _discarded, ...remaining } = current;
      return remaining;
    });
  }

  const loginHref = withAuthReturnPath(
    lp("/sign-in"),
    searchParams.has("title")
      ? `${lp("/watchlists")}?${searchParams.toString()}`
      : null,
  );
  const cloneFallbackTitle = t({
    id: "watchlists.pending.cloneTitle",
    comment: "Fallback title for a shared-watchlist copy saved in browser session state",
    message: "Shared watchlist copy",
  });
  const displayedWatchlists = isLoggedIn
    ? initialWatchlists
    : pendingWatchlists.map((entry) => sessionEntryOverview(entry, cloneFallbackTitle));
  const shareUnavailable = isLoggedIn
    ? undefined
    : t({
        id: "watchlists.actions.loginToShare",
        comment: "Disabled share tooltip for a browser-only watchlist",
        message: "Log in to share",
      });
  const hasLoadedActivity = displayedWatchlists.some(
    (watchlist) => activityById[watchlist.id] !== undefined,
  );

  return (
    <section aria-labelledby="watchlists-heading">
      <h1 id="watchlists-heading" className="mb-4 text-lg font-semibold">
        <Trans id="watchlists.page.title" comment="Title of the private watchlists page">
          Watchlists
        </Trans>
      </h1>

      {isPending ? (
        <div className="flex items-center justify-center py-8" role="status">
          <div className="size-7 rounded-full border-4 border-muted border-t-primary motion-safe:animate-spin" />
          <span className="sr-only">
            <Trans id="watchlists.load.loading" comment="Accessible loading status for the private watchlists overview route">
              Loading watchlists…
            </Trans>
          </span>
        </div>
      ) : (
        <div className="mx-auto w-full max-w-3xl" aria-busy={activityPending}>
          {!isLoggedIn && displayedWatchlists.length > 0 ? (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning-border/60 bg-warning-bg px-3 py-2.5 text-xs leading-relaxed text-warning" role="status">
              <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
              {t({
                id: "watchlists.pending.collectionWarning",
                comment: "Compact warning above anonymous watchlists stored in the current browser tab",
                message: "Saved in this browser until you log in. Sharing and alerts are unavailable. Closing this tab may remove them.",
              })}
            </div>
          ) : null}
          {hasLoadedActivity && !activityPending ? (
            <span className="sr-only" role="status" aria-live="polite">
              {t({
                id: "watchlists.activity.loaded",
                comment: "Screen-reader announcement after watchlist activity previews finish loading",
                message: "Watchlist activity finished loading.",
              })}
            </span>
          ) : null}
          {createError ? (
            <p className="mb-3 rounded-md border border-error/30 bg-error-bg px-3 py-2 text-sm text-error" role="alert">
              {createError}
            </p>
          ) : null}
          {displayedWatchlists.length === 0 ? (
            <p className="mb-4 text-sm text-muted">
              <Trans id="watchlists.page.empty" comment="Empty state when user has no watchlists">
                No watchlists yet. Create one to track jobs from your favorite companies.
              </Trans>
            </p>
          ) : null}
          <ScrollFade
            wrapperClassName="max-h-[max(12rem,calc(100dvh_-_8rem))]"
            className="overscroll-contain pr-2"
            fadeSize="h-8"
            deps={[displayedWatchlists.length, activityById]}
          >
            <ul className="space-y-3 pb-10 md:pb-6">
              {displayedWatchlists.map((watchlist) => (
                <li key={watchlist.id}>
                  <WatchlistCard
                    watchlist={watchlist}
                    activity={activityById[watchlist.id] ?? null}
                    activityPending={activityPending}
                    shareDisabledReason={shareUnavailable}
                    href={lp(`/watchlists/${watchlist.id}`)}
                    onShare={isLoggedIn
                      ? () => handleShare(watchlist.id)
                      : () => Promise.resolve()}
                    onDelete={isLoggedIn
                      ? () => handleDelete(watchlist.id)
                      : async () => discardPendingWatchlist(watchlist.id)}
                  />
                </li>
              ))}
              <li>
                <CreateWatchlistCard
                  onClick={isLoggedIn
                    ? () => void handleCreate()
                    : stageBlankWatchlist}
                  creating={isLoggedIn && creating}
                  disabled={isLoggedIn
                    ? limitReached
                    : pendingWatchlists.length >= PENDING_WATCHLIST_LIMIT}
                />
              </li>
            </ul>
          </ScrollFade>
        </div>
      )}
    </section>
  );
}
