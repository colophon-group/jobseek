"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, Loader2, LogIn } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSession } from "@/components/providers/SessionProvider";
import type {
  UserWatchlistOverview,
  WatchlistFilters,
} from "@/lib/actions/watchlists";
import {
  createWatchlist,
  createWatchlistFromHandoff,
} from "@/lib/actions/watchlists";
import {
  clearWatchlistSelection,
  selectOwnedWatchlist,
} from "@/lib/actions/watchlist-selection";
import type { WatchlistPageData } from "@/lib/services/watchlist-page-data";
import {
  WatchlistCard,
  CreateWatchlistCard,
} from "@/components/watchlist/watchlist-card";
import { Button } from "@/components/ui/Button";
import {
  parseEmploymentTypeParam,
  parseWorkModeParam,
} from "@/lib/search/query-params";
import { withAuthReturnPath } from "@/lib/auth-return";
import {
  broadcastWatchlistSelectionChanged,
  subscribeToWatchlistSelection,
} from "@/lib/watchlist-selection-client";
import { WatchlistViewPage } from "../[userSlug]/[watchlistSlug]/watchlist-view-page";

function commaSeparatedValues(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export function WatchlistsPage({
  initialWatchlists,
  initialPageData,
  selectionSync,
  limitReached,
  locale,
}: {
  initialWatchlists: UserWatchlistOverview[];
  initialPageData: WatchlistPageData | null;
  selectionSync: "none" | "replace" | "clear";
  limitReached: boolean;
  locale: string;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { isLoggedIn, isPending } = useSession();
  const searchParams = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const [watchlists, setWatchlists] = useState(initialWatchlists);
  const handoffAttemptedRef = useRef(false);
  const selectionSyncKeyRef = useRef<string | null>(null);

  useEffect(() => {
    return subscribeToWatchlistSelection(() => router.refresh());
  }, [router]);

  useEffect(() => {
    const selectedId = initialPageData?.detail.id ?? "empty";
    const syncKey = `${selectionSync}:${selectedId}`;
    if (selectionSync === "none" || selectionSyncKeyRef.current === syncKey) {
      return;
    }
    selectionSyncKeyRef.current = syncKey;

    const sync = selectionSync === "replace" && initialPageData
      ? selectOwnedWatchlist(initialPageData.detail.id)
      : clearWatchlistSelection();
    void sync.then(broadcastWatchlistSelectionChanged).catch(() => {
      selectionSyncKeyRef.current = null;
    });
  }, [initialPageData, selectionSync]);

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

  async function handleSelect(watchlistId: string) {
    if (selectingId || watchlistId === initialPageData?.detail.id) return;
    setSelectingId(watchlistId);
    try {
      const result = await selectOwnedWatchlist(watchlistId);
      if (result.ok) {
        broadcastWatchlistSelectionChanged();
        router.refresh();
      }
    } finally {
      setSelectingId(null);
    }
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
      if ("error" in result) return;

      const selected = await selectOwnedWatchlist(result.id);
      if (!selected.ok) return;
      broadcastWatchlistSelectionChanged();
      if (navigation === "replace" || searchParams.size > 0) {
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
    });
  }, [isLoggedIn, isPending, limitReached, searchParams]);

  const loginHref = withAuthReturnPath(
    lp("/sign-in"),
    searchParams.has("title")
      ? `${lp("/watchlists")}?${searchParams.toString()}`
      : null,
  );

  return (
    <div className="space-y-8">
      <section aria-labelledby="watchlists-heading">
        <h1 id="watchlists-heading" className="mb-4 text-lg font-semibold">
          <Trans id="watchlists.page.title" comment="Title of the private watchlists page">
            Watchlists
          </Trans>
        </h1>

        {!isLoggedIn ? (
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
        ) : watchlists.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center text-muted">
            <Eye size={32} aria-hidden="true" />
            <p className="text-sm">
              <Trans id="watchlists.page.empty" comment="Empty state when user has no watchlists">
                No watchlists yet. Create one to track jobs from your favorite companies.
              </Trans>
            </p>
            <button
              type="button"
              onClick={() => handleCreate()}
              disabled={creating}
              className="inline-flex cursor-pointer items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-contrast transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating && <Loader2 size={14} className="motion-safe:animate-spin" aria-hidden="true" />}
              <Trans id="watchlists.page.createFirst" comment="Button to create first watchlist">
                Create watchlist
              </Trans>
            </button>
          </div>
        ) : (
          <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-hide">
            {watchlists.map((watchlist) => (
              <WatchlistCard
                key={watchlist.id}
                watchlist={watchlist}
                active={watchlist.id === initialPageData?.detail.id}
                selecting={watchlist.id === selectingId}
                onSelect={() => handleSelect(watchlist.id)}
              />
            ))}
            <CreateWatchlistCard
              onClick={() => handleCreate()}
              creating={creating}
              disabled={limitReached}
            />
          </div>
        )}
      </section>

      {initialPageData && (
        <WatchlistViewPage
          key={initialPageData.detail.id}
          detail={initialPageData.detail}
          isOwner
          isPaidPlan={initialPageData.isPaidPlan}
          initialPostings={initialPageData.postings}
          initialTotal={initialPageData.total}
          yearTotal={initialPageData.yearTotal}
          locale={locale}
          resolvedLocations={initialPageData.resolvedLocations}
          resolvedOccupations={initialPageData.resolvedOccupations}
          resolvedSeniorities={initialPageData.resolvedSeniorities}
          resolvedTechnologies={initialPageData.resolvedTechnologies}
          jobLanguages={initialPageData.jobLanguages}
          languages={initialPageData.languages}
        />
      )}
    </div>
  );
}
