"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Eye, Loader2 } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { tooltipClass } from "@/components/ui/tooltip-styles";
import { useLocalePath } from "@/lib/useLocalePath";
import { useAuth } from "@/lib/useAuth";
import { createWatchlist, type WatchlistFilters } from "@/lib/actions/watchlists";
import type { SelectedLocation } from "@/components/search/location-pills";

type TaxonomyItem = { id: number; slug: string; name: string };

interface SaveSearchButtonProps {
  keywords: string[];
  locations: SelectedLocation[];
  occupations: TaxonomyItem[];
  seniorities: TaxonomyItem[];
  technologies?: TaxonomyItem[];
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
  salaryMin,
  salaryMax,
  salaryCurrency,
  experienceMin,
  experienceMax,
}: SaveSearchButtonProps) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { user, isLoggedIn } = useAuth();
  const [saving, setSaving] = useState(false);

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
      const title = parts.length > 0 ? parts.join(" · ") : "My search";

      const filters: WatchlistFilters = {};
      if (keywords.length > 0) filters.keywords = keywords;
      if (locations.length > 0) filters.locationSlugs = locations.map((l) => l.slug);
      if (occupations.length > 0) filters.occupationSlugs = occupations.map((o) => o.slug);
      if (seniorities.length > 0) filters.senioritySlugs = seniorities.map((s) => s.slug);
      if (technologies && technologies.length > 0) filters.technologySlugs = technologies.map((t) => t.slug);
      if (salaryMin != null) filters.salaryMin = salaryMin;
      if (salaryMax != null) filters.salaryMax = salaryMax;
      if (salaryCurrency) filters.salaryCurrency = salaryCurrency;
      if (experienceMin != null) filters.experienceMin = experienceMin;
      if (experienceMax != null) filters.experienceMax = experienceMax;

      const result = await createWatchlist({
        title,
        companyIds: [],
        filters,
      });

      if ("error" in result) {
        if (result.error === "limit_reached") {
          router.push(lp("/settings"));
        }
        return;
      }

      if ("slug" in result && user?.username) {
        router.push(lp(`/${user.username}/${result.slug}`));
      }
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

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
    <Tooltip.Root>
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
        <Tooltip.Content className={tooltipClass} sideOffset={5}>
          {tooltip}
          <Tooltip.Arrow className="fill-surface" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
    </Tooltip.Provider>
  );
}
