"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Eye, Loader2 } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  tooltipClass,
  tooltipWarningClass,
} from "@/components/ui/tooltip-styles";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSession } from "@/components/providers/SessionProvider";
import { createWatchlist } from "@/lib/actions/watchlists";
import { Button } from "@/components/ui/Button";
import type { SelectedLocation } from "@/lib/search/types";
import type { WorkMode } from "@/lib/search/types";
import { withAuthReturnPath } from "@/lib/auth-return";
import { buildSearchWatchlistDraft } from "@/lib/search/watchlist-draft";
import { stagePendingWatchlistEntry } from "@/lib/pending-watchlist";

type TaxonomyItem = { id: number; slug: string; name: string };

interface SaveSearchButtonProps {
  keywords: string[];
  locations: SelectedLocation[];
  occupations: TaxonomyItem[];
  seniorities: TaxonomyItem[];
  technologies?: TaxonomyItem[];
  employmentTypes?: string[];
  workMode?: WorkMode[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  /** Restrict the new watchlist to one company instead of all companies. */
  companyScope?: { id: string; name: string };
}

export function SaveSearchButton({
  keywords,
  locations,
  occupations,
  seniorities,
  technologies,
  employmentTypes,
  workMode,
  salaryMin,
  salaryMax,
  salaryCurrency,
  experienceMin,
  experienceMax,
  companyScope,
}: SaveSearchButtonProps) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { isLoggedIn } = useSession();
  const [saving, setSaving] = useState(false);
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const [limitNotice, setLimitNotice] = useState(false);
  const limitCloseRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(limitCloseRef.current), []);

  function showLimitNotice() {
    clearTimeout(limitCloseRef.current);
    setLimitNotice(true);
    setTooltipOpen(true);
    limitCloseRef.current = setTimeout(() => {
      setTooltipOpen(false);
      setLimitNotice(false);
    }, 3_000);
  }

  async function handleSave() {
    const draft = buildSearchWatchlistDraft({
      fallbackTitle: t({
        id: "watchlists.savedSearch.defaultTitle",
        comment: "Default watchlist title when saving a search without descriptive filters",
        message: "My search",
      }),
      keywords,
      locations,
      occupations,
      seniorities,
      technologies,
      employmentTypes,
      workMode,
      salaryMin,
      salaryMax,
      salaryCurrency,
      experienceMin,
      experienceMax,
      companyScope,
    });
    if (!isLoggedIn) {
      const returnPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      const staged = stagePendingWatchlistEntry({ kind: "create", draft });
      router.push(staged
        ? lp(`/watchlists/${staged.id}`)
        : withAuthReturnPath(lp("/sign-in"), returnPath));
      return;
    }

    setSaving(true);
    try {
      const result = await createWatchlist(draft);

      if ("error" in result) {
        if (result.error === "limit_reached") {
          showLimitNotice();
        }
        return;
      }

      router.push(lp(`/watchlists/${result.id}`));
    } catch {
      // Keep the current route unchanged. A later click is an explicit retry.
    } finally {
      setSaving(false);
    }
  }

  const label = t({
    id: "search.saveSearch.label",
    comment: "Button label to save current search as a watchlist",
    message: "Save this search",
  });

  const tooltip = isLoggedIn
    ? t({
        id: "search.saveSearch.tooltip",
        comment: "Tooltip explaining save search creates a watchlist",
        message: "Create a watchlist from your current filters",
      })
    : t({
        id: "search.saveSearch.tooltipLogin",
        comment: "Tooltip when user needs to log in to save search",
        message: "Save this search now and add it after login",
      });
  const limitLabel = t({
    id: "watchlists.card.limitReached",
    comment: "Warning tooltip when the account-wide watchlist limit is reached",
    message: "Maximum of 10 watchlists reached",
  });

  return (
    <>
      <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root
        open={tooltipOpen}
        onOpenChange={(open) => {
          setTooltipOpen(open);
          if (!open) setLimitNotice(false);
        }}
      >
        <Tooltip.Trigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={saving}
            className="h-8 shrink-0 gap-1.5 px-3 text-xs text-foreground"
          >
            {saving ? (
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            ) : (
              <Eye size={14} aria-hidden="true" />
            )}
            {label}
          </Button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className={limitNotice ? tooltipWarningClass : tooltipClass}
            side="top"
            sideOffset={5}
          >
            <span className="flex items-center gap-1.5">
              {limitNotice ? (
                <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
              ) : null}
              {limitNotice ? limitLabel : tooltip}
            </span>
            {!limitNotice ? <Tooltip.Arrow className="fill-surface" /> : null}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
      </Tooltip.Provider>
    </>
  );
}
