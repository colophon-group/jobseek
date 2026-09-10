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
import { createWatchlist, type WatchlistFilters } from "@/lib/actions/watchlists";
import type { SelectedLocation } from "@/lib/search/types";
import type { WorkMode } from "@/lib/search/types";

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
    if (!isLoggedIn) {
      router.push(lp("/sign-in"));
      return;
    }

    setSaving(true);
    try {
      // Build a descriptive title from the active filters
      const parts: string[] = [];
      if (keywords.length > 0) parts.push(keywords.join(", "));
      if (locations.length > 0) parts.push(locations.map((l) => l.name).join(", "));
      if (occupations.length > 0) parts.push(occupations.map((o) => o.name).join(", "));
      const title = parts.length > 0
        ? parts.join(" · ")
        : t({
            id: "watchlists.savedSearch.defaultTitle",
            comment: "Default watchlist title when saving a search without descriptive filters",
            message: "My search",
          });

      const filters: WatchlistFilters = {};
      if (keywords.length > 0) filters.keywords = keywords;
      if (locations.length > 0) filters.locationSlugs = locations.map((l) => l.slug);
      if (occupations.length > 0) filters.occupationSlugs = occupations.map((o) => o.slug);
      if (seniorities.length > 0) filters.senioritySlugs = seniorities.map((s) => s.slug);
      if (technologies && technologies.length > 0) filters.technologySlugs = technologies.map((t) => t.slug);
      if (employmentTypes && employmentTypes.length > 0) filters.employmentType = employmentTypes;
      if (workMode && workMode.length > 0) filters.workMode = workMode;
      if (salaryMin != null) filters.salaryMin = salaryMin;
      if (salaryMax != null) filters.salaryMax = salaryMax;
      if (salaryCurrency) filters.salaryCurrency = salaryCurrency;
      if (experienceMin != null) filters.experienceMin = experienceMin;
      if (experienceMax != null) filters.experienceMax = experienceMax;

      const result = await createWatchlist({
        title,
        companyIds: [],
        filters,
        isPublic: false,
      });

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
        message: "Log in to save this search as a watchlist",
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
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex shrink-0 cursor-pointer items-center gap-1 text-xs text-primary transition-colors hover:text-primary/80 disabled:opacity-50"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
            {label}
          </button>
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
