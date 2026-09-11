"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, LogIn } from "lucide-react";
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
  deleteWatchlist,
  shareWatchlist,
} from "@/lib/actions/watchlists";
import {
  WatchlistCard,
  CreateWatchlistCard,
} from "@/components/watchlist/watchlist-card";
import { Button } from "@/components/ui/Button";
import { ScrollFade } from "@/components/ui/scroll-fade";
import {
  parseEmploymentTypeParam,
  parseWorkModeParam,
} from "@/lib/search/query-params";
import { withAuthReturnPath } from "@/lib/auth-return";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { copyTextToClipboard } from "@/lib/copy-text-to-clipboard";

function commaSeparatedValues(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
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
    setActivityById({});
    setActivityPending(isLoggedIn && initialWatchlists.length > 0);
    if (!isLoggedIn || initialWatchlists.length === 0) return;

    const controller = new AbortController();
    let disposed = false;
    const timeoutId = setTimeout(() => controller.abort(), 12_000);

    void fetch(`/api/web/watchlists/counts?locale=${encodeURIComponent(locale)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Watchlist previews failed: ${response.status}`);
        return response.json() as Promise<{
          previews?: Record<string, UserWatchlistActivityPreview>;
        }>;
      })
      .then(({ previews }) => {
        if (previews && !disposed) setActivityById(previews);
      })
      .catch(() => {
        // The overview remains navigable when optional live activity fails.
      })
      .finally(() => {
        clearTimeout(timeoutId);
        if (!disposed) setActivityPending(false);
      });

    return () => {
      disposed = true;
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [initialWatchlists, isLoggedIn, locale]);

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

  useEffect(() => {
    if (isPending || handoffAttemptedRef.current) return;

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
  }, [currencyRates, isLoggedIn, isPending, limitReached, searchParams]);

  const loginHref = withAuthReturnPath(
    lp("/sign-in"),
    searchParams.has("title")
      ? `${lp("/watchlists")}?${searchParams.toString()}`
      : null,
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
      ) : !isLoggedIn ? (
        <div className="flex flex-col items-center gap-3 py-8 text-center text-muted">
          <Eye size={32} aria-hidden="true" />
          <p className="text-sm">
            <Trans
              id="watchlists.page.loginPrompt"
              comment="Prompt for non-logged-in users to sign in to create watchlists"
            >
              Sign in to create and manage your own watchlists.
            </Trans>
          </p>
          <Button href={loginHref} variant="primary" size="sm" className="gap-2">
            <LogIn size={16} aria-hidden="true" />
            {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
          </Button>
        </div>
      ) : (
        <div className="mx-auto w-full max-w-3xl" aria-busy={activityPending}>
          {initialWatchlists.length > 0 && !activityPending ? (
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
          {initialWatchlists.length === 0 ? (
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
            deps={[initialWatchlists.length, activityById]}
          >
            <ul className="space-y-3 pb-10 md:pb-6">
              {initialWatchlists.map((watchlist) => (
                <li key={watchlist.id}>
                  <WatchlistCard
                    watchlist={watchlist}
                    activity={activityById[watchlist.id] ?? null}
                    activityPending={activityPending}
                    href={lp(`/watchlists/${watchlist.id}`)}
                    onShare={() => handleShare(watchlist.id)}
                    onDelete={() => handleDelete(watchlist.id)}
                  />
                </li>
              ))}
              <li>
                <CreateWatchlistCard
                  onClick={() => void handleCreate()}
                  creating={creating}
                  disabled={limitReached}
                />
              </li>
            </ul>
          </ScrollFade>
        </div>
      )}
    </section>
  );
}
