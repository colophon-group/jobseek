"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, Loader2, LogIn } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSession } from "@/components/providers/SessionProvider";
import type {
  PublicWatchlistEntry,
  UserWatchlistOverview,
  WatchlistFilters,
} from "@/lib/actions/watchlists";
import {
  createWatchlist,
  createWatchlistFromHandoff,
} from "@/lib/actions/watchlists";
import { WatchlistCard, CreateWatchlistCard } from "@/components/watchlist/watchlist-card";
import { PublicWatchlistSearch } from "@/components/watchlist/public-watchlist-search";
import {
  WatchlistLimitModal,
  useWatchlistLimitModal,
} from "@/components/watchlist/watchlist-limit-modal";
import { Button } from "@/components/ui/Button";
import {
  parseEmploymentTypeParam,
  parseWorkModeParam,
} from "@/lib/search/query-params";
import { withAuthReturnPath } from "@/lib/auth-return";

function commaSeparatedValues(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export function WatchlistsPage({
  initialWatchlists,
  initialPopularWatchlists = [],
  initialPopularTotal = 0,
  username,
  limitReached,
  locale,
}: {
  initialWatchlists: UserWatchlistOverview[];
  initialPopularWatchlists?: PublicWatchlistEntry[];
  initialPopularTotal?: number;
  username: string | null;
  limitReached: boolean;
  locale: string;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { user, isLoggedIn, isPending } = useSession();
  const searchParams = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [watchlists, setWatchlists] = useState(initialWatchlists);
  const [atLimit, setAtLimit] = useState(limitReached);
  const limitNotice = useWatchlistLimitModal();
  const handoffAttemptedRef = useRef(false);

  useEffect(() => {
    setWatchlists(initialWatchlists);
    if (!isLoggedIn || initialWatchlists.length === 0) return;

    const controller = new AbortController();
    let disposed = false;
    const timeoutId = setTimeout(() => controller.abort(), 12_000);

    void fetch(`/api/web/watchlists/counts?locale=${encodeURIComponent(locale)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Watchlist counts failed: ${response.status}`);
        return response.json() as Promise<{ counts?: Record<string, unknown> }>;
      })
      .then(({ counts }) => {
        if (!counts || disposed) return;
        setWatchlists((current) =>
          current.map((watchlist) => {
            const count = counts[watchlist.id];
            return {
              ...watchlist,
              activeJobCount:
                typeof count === "number" ? count : watchlist.activeJobCount,
            };
          }),
        );
      })
      .catch(() => {
        // Company counts remain visible when the optional live count fails.
      })
      .finally(() => clearTimeout(timeoutId));

    return () => {
      disposed = true;
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [initialWatchlists, isLoggedIn, locale]);

  useEffect(() => setAtLimit(limitReached), [limitReached]);

  async function handleCreate(
    prefill?: {
      title?: string;
      description?: string;
      filters?: WatchlistFilters;
      companySlugs?: string[];
    },
    navigation: "push" | "replace" = "push",
  ) {
    if (creating || !isLoggedIn) return;
    if (atLimit) {
      limitNotice.show();
      return;
    }
    setCreating(true);
    try {
      const result = prefill?.companySlugs !== undefined
        ? await createWatchlistFromHandoff({
            title: prefill.title || "New watchlist",
            description: prefill.description,
            companySlugs: prefill.companySlugs,
            filters: prefill.filters,
          })
        : await createWatchlist({
            title: prefill?.title || "New watchlist",
            description: prefill?.description,
            companyIds: [],
            filters: prefill?.filters,
            isPublic: false,
          });
      if ("error" in result) {
        // A concurrent ownership-increasing write can make the server the
        // first place this mount learns about the cap. Keep the overview's
        // Create state aligned with that authoritative result.
        if (result.error === "limit_reached") {
          setAtLimit(true);
          limitNotice.show();
        }
        return;
      }
      if ("slug" in result && (username ?? user?.username)) {
        const destination = lp(`/${username ?? user?.username}/${result.slug}`);
        if (navigation === "replace") {
          router.replace(destination);
        } else {
          router.push(destination);
        }
      } else if (navigation === "replace") {
        // A successful handoff without a navigable owner/slug still needs to
        // lose the mutating query string so refresh/back cannot create again.
        router.replace(lp("/watchlists"));
      } else {
        router.refresh();
      }
    } finally {
      setCreating(false);
    }
  }

  const runWatchlistHandoff = useEffectEvent(
    (prefill: {
      title: string;
      description?: string;
      filters?: WatchlistFilters;
      companySlugs: string[];
    }) => handleCreate(prefill, "replace"),
  );

  // Auto-create a watchlist from URL params (for example, the URL emitted by
  // /api/v1/watchlist/create). Wait for the asynchronous session bootstrap,
  // then claim this mounted handoff before any mutation or modal side effect.
  // The ref lets the effect react to bootstrap without allowing later context
  // or URL identity changes to create the same watchlist twice.
  useEffect(() => {
    if (isPending || handoffAttemptedRef.current) return;

    const title = searchParams.get("title");
    if (!title) return;

    handoffAttemptedRef.current = true;
    if (!isLoggedIn) return;
    if (atLimit) {
      limitNotice.show();
      return;
    }

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
    if (salcur) filters.salaryCurrency = salcur;
    if (sal) {
      const [minStr, maxStr] = sal.split("-");
      if (minStr) filters.salaryMin = parseInt(minStr, 10);
      if (maxStr) filters.salaryMax = parseInt(maxStr, 10);
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
      // A reload is then an explicit retry, while this mount stays one-shot.
    });
  }, [atLimit, isLoggedIn, isPending, searchParams]);

  const loginHref = withAuthReturnPath(
    lp("/sign-in"),
    searchParams.has("title")
      ? `${lp("/watchlists")}?${searchParams.toString()}`
      : null,
  );

  return (
    <>
    <div className="space-y-8">
      {/* My watchlists */}
      <div>
        <h1 className="mb-4 text-lg font-semibold">
          <Trans id="watchlists.page.title" comment="Title of the watchlists exploration page">
            Watchlists
          </Trans>
        </h1>

        {!isLoggedIn ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center text-muted">
            <Eye size={32} />
            <p className="text-sm">
              <Trans
                id="watchlists.page.loginPrompt"
                comment="Prompt for non-logged-in users to sign in to create watchlists"
              >
                Sign in to create and manage your own watchlists.
              </Trans>
            </p>
            <Button href={loginHref} variant="primary" size="sm" className="gap-2">
              <LogIn size={16} />
              {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
            </Button>
          </div>
        ) : watchlists.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center text-muted">
            <Eye size={32} />
            <p className="text-sm">
              <Trans
                id="watchlists.page.empty"
                comment="Empty state when user has no watchlists"
              >
                No watchlists yet. Create one to track jobs from your favorite
                companies.
              </Trans>
            </p>
            <button
              type="button"
              onClick={() => handleCreate()}
              disabled={creating}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-contrast transition-opacity hover:opacity-90 disabled:opacity-50 cursor-pointer"
            >
              {creating && <Loader2 size={14} className="animate-spin" />}
              <Trans id="watchlists.page.createFirst" comment="Button to create first watchlist">
                Create watchlist
              </Trans>
            </button>
          </div>
        ) : (
          <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-hide">
            {watchlists.map((wl) => (
              <WatchlistCard
                key={wl.id}
                watchlist={wl}
                ownerUsername={username}
              />
            ))}
            <CreateWatchlistCard
              onClick={handleCreate}
              onLimitReached={limitNotice.show}
              creating={creating}
              limitReached={atLimit}
            />
          </div>
        )}
      </div>

      {/* Public search — always visible */}
      <PublicWatchlistSearch
        initialWatchlists={initialPopularWatchlists}
        initialTotal={initialPopularTotal}
      />
    </div>
    <WatchlistLimitModal
      open={limitNotice.open}
      onOpenChange={limitNotice.setOpen}
    />
    </>
  );
}
