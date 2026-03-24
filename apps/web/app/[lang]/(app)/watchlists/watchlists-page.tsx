"use client";

import { useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, Loader2, LogIn } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { useAuth } from "@/lib/useAuth";
import type { WatchlistSummary, WatchlistFilters } from "@/lib/actions/watchlists";
import { createWatchlist } from "@/lib/actions/watchlists";
import { WatchlistCard, CreateWatchlistCard } from "@/components/watchlist/watchlist-card";
import { PublicWatchlistSearch } from "@/components/watchlist/public-watchlist-search";
import { Button } from "@/components/ui/Button";

export function WatchlistsPage({
  initialWatchlists,
  username,
  limitReached,
}: {
  initialWatchlists: WatchlistSummary[];
  username: string | null;
  limitReached: boolean;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { user, isLoggedIn } = useAuth();
  const searchParams = useSearchParams();
  const [creating, setCreating] = useState(false);

  async function handleCreate(prefill?: { title?: string; description?: string; filters?: WatchlistFilters }) {
    if (creating || !isLoggedIn) return;
    if (limitReached) {
      router.push(lp("/settings"));
      return;
    }
    setCreating(true);
    try {
      const result = await createWatchlist({
        title: prefill?.title || "New watchlist",
        description: prefill?.description,
        companyIds: [],
        filters: prefill?.filters,
      });
      if ("slug" in result && (username ?? user?.username)) {
        router.push(lp(`/${username ?? user?.username}/${result.slug}`));
      } else {
        router.refresh();
      }
    } finally {
      setCreating(false);
    }
  }

  // Auto-create watchlist from URL params (e.g. from /api/v1/watchlist/create)
  useEffect(() => {
    const title = searchParams.get("title");
    if (!title || !isLoggedIn || limitReached) return;

    const q = searchParams.get("q");
    const loc = searchParams.get("loc");
    const occ = searchParams.get("occ");
    const sen = searchParams.get("sen");
    const tech = searchParams.get("tech");
    const sal = searchParams.get("sal");
    const exp = searchParams.get("exp");
    const salcur = searchParams.get("salcur");

    const filters: WatchlistFilters = {};
    if (q) filters.keywords = q.split(",").filter(Boolean);
    if (loc) filters.locationSlugs = loc.split(",").filter(Boolean);
    if (occ) filters.occupationSlugs = occ.split(",").filter(Boolean);
    if (sen) filters.senioritySlugs = sen.split(",").filter(Boolean);
    if (tech) filters.technologySlugs = tech.split(",").filter(Boolean);
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

    handleCreate({
      title,
      description: searchParams.get("description") ?? undefined,
      filters: Object.keys(filters).length > 0 ? filters : undefined,
    });
  }, []);

  return (
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
            <Button href={lp("/sign-in")} variant="primary" size="sm" className="gap-2">
              <LogIn size={16} />
              {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
            </Button>
          </div>
        ) : initialWatchlists.length === 0 ? (
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
            {initialWatchlists.map((wl) => (
              <WatchlistCard
                key={wl.id}
                watchlist={wl}
                ownerUsername={username}
              />
            ))}
            <CreateWatchlistCard
              onClick={handleCreate}
              creating={creating}
              disabled={limitReached}
            />
          </div>
        )}
      </div>

      {/* Public search — always visible */}
      <PublicWatchlistSearch />
    </div>
  );
}
